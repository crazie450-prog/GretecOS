"""Where raw inputs come from (WO-3.10 F2, D427 (2), (6)), on the WO-3.3 fixture DB
(tests/unit/planner/test_graph.py) plus one made-up ore mix. No pack data.

- An ore an ore mix places in a reachable dimension is a ``raw`` boundary with a ``vein:``
  source; one only unreachable dimensions place rejects the recipes that consume it, with the
  item and the dimension in the reason. Stock, line outputs and the request's boundary win.
- Every ore block of the mix's material counts, a Netherrack ore only in the Nether.
- A curated ``dimension:<name>`` row gates the same way; an oredict slot whose every member is
  unreachable is rejected.
- A plan warns about a raw import GOS has no source for, and not about a sourced one.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import quests as bq
from gregtec.ingest.quests import ALWAYS_DIMENSIONS
from gregtec.planner import graph, plan
from gregtec.planner.request import Request


def _graph_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_graph.py")
    spec = importlib.util.spec_from_file_location("_f2_graph_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tg = _graph_fixture()
T, ORE_A, DUST_A, DUST_B = tg.T, tg.ORE_A, tg.DUST_A, tg.DUST_B
LOG_A, LOG_B, WATER = tg.LOG_A, tg.LOG_B, tg.WATER
NETHER_ORE_A = f"{T}@6"
MOON = ALWAYS_DIMENSIONS | {"moon"}


@pytest.fixture(scope="module")
def src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tg.make_home(tmp_path_factory.mktemp("raw-sources-home"))


@pytest.fixture
def conn(src: Path, tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A writable copy of the fixture DB (its own path: its own reader cache scope)."""
    dst = tmp_path / "gos.sqlite"
    a, b = access.connect(src, readonly=True), sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    c = access.connect(dst)
    try:
        yield c
    finally:
        c.close()


def add_mix(c: sqlite3.Connection, dims: list[str], meta: int = 5) -> None:
    """An ore mix of Testium (Ore A's material) in ``dims``, as `gos ores load` writes it."""
    c.execute(
        "INSERT OR REPLACE INTO ore_mix(id, name, dims, ymin, ymax, weight, density, size,"
        " ores) VALUES ('ore.mix.testium', 'Testium', ?, 10, 40, 40, 3, 24, ?)",
        (
            json.dumps({d: {"ids": []} for d in dims}),
            json.dumps([{"layer": "primary", "material": "Testium", "name": "Ore A",
                         "item": T, "meta": meta}]),
        ),
    )  # fmt: skip


def reasons(g: graph.Graph, key: str) -> dict[str, str]:
    return {
        c.recipe: c.reason for c in g.item(key).candidates if c.status == "rejected"
    }


def test_an_ore_only_an_unreachable_dimension_places_is_rejected(
    conn: sqlite3.Connection,
) -> None:
    add_mix(conn, ["moon"])
    cr1 = tg.ref_of(conn, "chemicalreactor", 1)
    g = tg.build_graph(conn, Request(DUST_A))
    assert reasons(g, DUST_A)[cr1] == "Ore A: moon, not reachable (D427)"
    assert ORE_A not in {i.id for i in g.items}
    # Once the moon is reachable, Ore A is a raw input with its vein as the source.
    g = tg.build_graph(conn, Request(DUST_A), tg.Base(dims=MOON))
    assert cr1 in g.item(DUST_A).chosen
    ore = g.item(ORE_A)
    assert ore.boundary == "raw" and ore.source == "vein:moon"
    assert g.item(WATER).source == "pump"  # data/curated/raw_inputs.tsv
    assert (
        g.canonical()
        == tg.build_graph(conn, Request(DUST_A), tg.Base(dims=MOON)).canonical()
    )


def test_what_he_has_is_never_gated(conn: sqlite3.Connection) -> None:
    add_mix(conn, ["moon"])
    cr1 = tg.ref_of(conn, "chemicalreactor", 1)
    for g in (
        tg.build_graph(conn, Request(DUST_A), tg.Base(stocked={ORE_A: 1})),
        tg.build_graph(conn, Request(DUST_A), tg.Base(lines={ORE_A: 0.5})),
        tg.build_graph(conn, Request(DUST_A, boundary=(ORE_A,))),
    ):
        assert cr1 in g.item(DUST_A).chosen
        assert g.item(ORE_A).boundary in ("stock", "line", "request")


def test_no_ore_mixes_gate_nothing(conn: sqlite3.Connection) -> None:
    assert graph.sources(conn).vein == {}
    g = tg.build_graph(conn, Request(DUST_A))
    assert tg.ref_of(conn, "chemicalreactor", 1) in g.item(DUST_A).chosen
    assert g.item(ORE_A).boundary == "raw" and g.item(ORE_A).source is None


def test_a_netherrack_ore_is_placed_in_the_nether_only(
    conn: sqlite3.Connection,
) -> None:
    conn.executemany(
        "INSERT INTO item(registry_name, meta, name_en, gt_prefix, gt_material)"
        " VALUES (?, ?, ?, ?, 'Testium')",
        [
            (T, 6, "Nether Ore A", "oreNetherrack"),
            (T, 7, "Horus Ore A", "oreHorus"),  # another planet's rock
        ],
    )
    add_mix(conn, ["Overworld"])
    vein = graph.sources(conn).vein
    # Ore A by its exact key and its material; the Netherrack one: Testium, but not here.
    assert vein[ORE_A] == ("Overworld",) and vein[NETHER_ORE_A] == ()
    assert f"{T}@7" not in vein  # a stone not in STONE_DIMS: only by its exact key
    before = graph.sources(conn).digest()
    add_mix(conn, ["Nether", "Overworld"])
    vein = graph.sources(conn).vein
    assert vein[NETHER_ORE_A] == ("Nether",)
    assert vein[ORE_A] == ("Nether", "Overworld")
    assert graph.sources(conn).digest() != before  # the effort cache key sees it


def test_a_curated_dimension_source_and_an_unreachable_slot(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    (mixer1,) = tg.build_graph(conn, Request(DUST_B)).item(DUST_B).chosen  # logs
    orig = graph.raw_sources
    twilight = "dimension:Twilight Forest"
    monkeypatch.setattr(
        graph, "raw_sources", lambda: {**orig(), LOG_A: twilight, LOG_B: twilight}
    )
    g = tg.build_graph(conn, Request(DUST_B))
    assert reasons(g, DUST_B)[mixer1] == (
        "ore:logTest: every member: Twilight Forest, not reachable (D427)"
    )
    # One member reachable: the slot keeps it, and the other never enters the graph.
    monkeypatch.setattr(graph, "raw_sources", lambda: {**orig(), LOG_B: twilight})
    g = tg.build_graph(conn, Request(DUST_B))
    assert mixer1 in g.item(DUST_B).chosen
    assert g.item("ore:logTest").members == (LOG_A,) and LOG_B not in {
        i.id for i in g.items
    }
    tf = tg.Base(dims=ALWAYS_DIMENSIONS | {"Twilight Forest"})
    monkeypatch.setattr(
        graph, "raw_sources", lambda: {**orig(), LOG_A: twilight, LOG_B: twilight}
    )
    g = tg.build_graph(conn, Request(DUST_B), tf)
    assert mixer1 in g.item(DUST_B).chosen
    assert g.item(LOG_A).boundary == "raw" and g.item(LOG_A).source == twilight


def test_a_raw_import_with_no_source_is_warned(conn: sqlite3.Connection) -> None:
    def warnings(base: object) -> str:
        d = plan.design(conn, Request(DUST_A, qty=1, tier_cap="MV"), base)  # type: ignore[arg-type]
        p = d.plans[0]
        assert ORE_A in {b["id"] for b in p["boundary"]["in"]}
        return "\n".join(p["warnings"])

    found = warnings(tg.Base())
    assert f"imports {ORE_A} (raw: no recipe makes it and GOS has no source" in found
    assert WATER not in found  # curated: pumped
    add_mix(conn, ["moon"])
    assert ORE_A not in warnings(tg.Base(dims=MOON))  # a vein the player can reach


def test_every_curated_dimension_has_an_unlock() -> None:
    """A raw_inputs.tsv ``dimension:<name>`` is always reachable or has an unlock quest."""
    unlocks = {
        u.dimension for u in bq.read_dimension_unlocks(quest_db.DIMENSION_UNLOCKS)
    }
    dims = {
        s.removeprefix(graph.DIMENSION_SOURCE)
        for s in graph.raw_sources().values()
        if s.startswith(graph.DIMENSION_SOURCE)
    }
    assert "Twilight Forest" in dims  # the Steeleaf nugget (WO-3.10)
    assert dims <= unlocks | ALWAYS_DIMENSIONS
