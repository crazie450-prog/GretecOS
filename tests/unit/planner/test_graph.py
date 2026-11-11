"""The recipe graph builder, request and chain templates (WO-3.3) on a small fixture DB.

The export run is self-authored, shaped like GregtecLink's (docs/contracts/gregteclink-export-v1.md)
and loaded through the real ``gos db build``, so the graph reads exactly the tables the loaders
write. Every item, recipe and machine in it is made up here; no pack data.

Recipes making the target, ``test:item@1`` (Widget), at tier cap MV (2):

- ``mixer/0``: 2 Dust A + 1 Dust B + circuit 1 (not consumed), LV;
- ``chemicalreactor/0``: 1 Plate A + 1000 L acid, LV;
- ``chemicalreactor/5``: 1 Plate A, LV, cleanroom (special value -200);
- ``electrolyzer/0``: 1 Ore A -> Widget at 50 %, MV (chanced only);
- ``electrolyzer/2``: IV, above the cap; ``chemicalreactor/4``: acid in two slots (D191);
- a disabled and a fake one, never candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gregtec.agents import prefetch
from gregtec.db import access, build
from gregtec.db import quests as quest_db
from gregtec.ingest import collect
from gregtec.ingest.normalize import CIRCUIT
from gregtec.planner import chains, graph, graphcli
from gregtec.planner.gtmath.parallel import Supply
from gregtec.planner.request import (
    BaseInputs,
    Constraints,
    DefaultBaseInputs,
    Pin,
    Request,
    RequestError,
    parse_duration,
    parse_qty,
    tier_index,
)

RUN = "20261111T120000Z"
T = "test:item"
BASIC = "gregtech.api.metatileentity.implementations.MTEBasicMachineWithRecipe"
WIDGET, PLATE, DUST_A, DUST_B, ORE_A = (f"{T}@{n}" for n in (1, 2, 3, 4, 5))
LOG_A, LOG_B, ACID_CELL, EMPTY_CELL = (f"{T}@{n}" for n in (8, 9, 10, 11))
ACID, GAS, WATER = "fluid:testacid", "fluid:testgas", "fluid:water"
MV = 2


def r(map_: str, idx: int) -> dict[str, object]:
    return {"map": map_, "idx": idx}


def ref(meta: int, n: int = 1, item: str = T, **kw: object) -> dict[str, object]:
    return {"item": item, "meta": meta, "n": n, **kw}


def gt(map_: str, idx: int, eut: int, duration: int, **kw: object) -> dict[str, object]:
    line: dict[str, object] = {
        "map": f"gt.recipe.{map_}",
        "idx": idx,
        "category": kw.pop("category", f"gt.recipe.{map_}"),
        "eut": eut,
        "duration": duration,
        "special": kw.pop("special", 0),
        "in": kw.pop("ins", []),
        "out": kw.pop("outs", []),
        "fin": kw.pop("fin", []),
        "fout": kw.pop("fout", []),
        "enabled": True,
        "hidden": False,
        "fake": False,
        "can_be_buffered": True,
        "needs_empty_output": False,
        "nbt_sensitive": False,
    }
    line.update(kw)
    return line


def fl(name: str, mb: int) -> dict[str, object]:
    return {"fluid": name, "mb": mb}


def recipes() -> list[dict[str, object]]:
    return [
        gt("mixer", 0, 30, 100, ins=[ref(3, 2), ref(4), ref(1, 0, CIRCUIT)], outs=[ref(1)]),
        gt("chemicalreactor", 0, 30, 200, ins=[ref(2)], fin=[fl("testacid", 1000)],
           outs=[ref(1)]),
        gt("electrolyzer", 0, 120, 50, ins=[ref(5)], outs=[ref(1)], out_chance=[5000]),
        gt("electrolyzer", 2, 8192, 20, ins=[ref(3)], outs=[ref(1)]),
        gt("chemicalreactor", 4, 30, 20, fin=[fl("testacid", 100), fl("testacid", 100)],
           outs=[ref(1, 2)]),
        gt("chemicalreactor", 5, 30, 40, special=-200, ins=[ref(2)], outs=[ref(1)]),
        gt("chemicalreactor", 6, 30, 40, enabled=False, ins=[ref(3)], outs=[ref(1)]),
        gt("chemicalreactor", 7, 30, 40, fake=True, ins=[ref(4)], outs=[ref(1)]),
        # Dust A from ore; Plate A from Dust A; Dust B from any log (an oredict slot).
        gt("chemicalreactor", 1, 30, 100, ins=[ref(5)], fin=[fl("water", 100)],
           outs=[ref(3, 2)]),
        gt("mixer", 2, 16, 60, ins=[ref(3)], outs=[ref(2)]),
        gt("mixer", 1, 16, 60, ins=[ref(8, ore="logTest", alt=[ref(8), ref(9)])],
           outs=[ref(4)]),
        # A loop: acid from gas and water, gas from acid (and a canner, ranked last).
        gt("chemicalreactor", 3, 30, 110, fin=[fl("testgas", 1000), fl("water", 1000)],
           fout=[fl("testacid", 2000)]),
        gt("electrolyzer", 1, 60, 100, fin=[fl("testacid", 1000)], fout=[fl("testgas", 1000)]),
        gt("canner", 0, 1, 16, ins=[ref(10)], outs=[ref(11)], fout=[fl("testacid", 1000)]),
        # Recycling: Widget back to Dust A (GT's recycling category, D142 (2)).
        gt("arcfurnace", 0, 30, 50, category="gt.recipe.category.arc_furnace_recycling",
           ins=[ref(1)], outs=[ref(3)]),
        # EU/t 0 (a machine that powers it its own way): kept, ranked last (D189).
        gt("mixer", 4, 0, 70, ins=[ref(4)], outs=[ref(1)]),
    ]  # fmt: skip


MAPS = ("mixer", "chemicalreactor", "electrolyzer", "canner", "arcfurnace")


def recipe_maps() -> list[dict[str, object]]:
    out = []
    for m in MAPS:
        cats = [{"name": f"gt.recipe.{m}", "owner_mod": "gregtech"}]
        if m == "arcfurnace":
            cats.append(
                {"name": "gt.recipe.category.arc_furnace_recycling", "owner_mod": "gregtech"}
            )
        out.append(
            {
                "name": f"gt.recipe.{m}",
                "amperage": 1,
                "backend": "gregtech.api.recipe.RecipeMapBackend",
                "frontend": "gregtech.api.recipe.RecipeMapFrontend",
                "ui": {"max_item_in": 6, "max_item_out": 6, "max_fluid_in": 2,
                       "max_fluid_out": 2},
                "nei_register": True,
                "owner_mod": "gregtech",
                "categories": cats,
                "count": 1,
            }
        )  # fmt: skip
    return out


def items() -> list[dict[str, object]]:
    rows = [
        (1, "Widget", [], None),
        (2, "Plate A", [], ("plate", "Testium")),
        (3, "Dust A", [], ("dust", "Testium")),
        (4, "Dust B", [], ("dust", "Otherium")),
        (5, "Ore A", [], ("ore", "Testium")),
        (8, "Oak Test Log", ["logTest"], None),
        (9, "Birch Test Log", ["logTest"], None),
        (10, "Acid Cell", [], ("cell", "Acid")),
        (11, "Empty Test Cell", [], None),
    ]
    out: list[dict[str, object]] = []
    for meta, name, ore, assoc in rows:
        line: dict[str, object] = {
            "item": T,
            "meta": meta,
            "unlocalized": f"{T}.{meta}",
            "name": name,
            "ore": ore,
        }
        if assoc:
            line["gt"] = {"prefix": assoc[0], "material": assoc[1], "amount": 3628800}
        out.append(line)
    out.append(
        {"item": CIRCUIT, "meta": 1, "unlocalized": "circuit", "name": "Circuit 1", "ore": []}
    )
    return out


def machines() -> list[dict[str, object]]:
    def m(mte: int, map_: str, tier: int, mod: str = "gregtech") -> dict[str, object]:
        return {
            "mte_id": mte,
            "item": "gregtech:gt.blockmachines",
            "meta": mte,
            "class": BASIC,
            "mod": mod,
            "meta_name": f"basicmachine.{map_}.tier.0{tier}",
            "name": f"Test {map_} {tier}",
            "tier": tier,
            "multiblock": False,
            "recipe_maps": [f"gt.recipe.{map_}"],
            "tooltip": [],
        }

    return [
        m(101, "mixer", 1),
        m(102, "chemicalreactor", 1),
        m(103, "electrolyzer", 2),
        m(104, "canner", 1, mod="testaddon"),
        m(105, "arcfurnace", 1),
    ]


def fluids() -> list[dict[str, object]]:
    return [
        {"fluid": f, "unlocalized": f"fluid.{f}", "name": f.title(), "temperature": 300,
         "density": 1000, "viscosity": 1000, "gaseous": f == "testgas"}
        for f in ("water", "testacid", "testgas")
    ]  # fmt: skip


def jsonl(lines: list[dict[str, object]]) -> bytes:
    return "".join(json.dumps(x) + "\n" for x in lines).encode()


def make_home(home: Path) -> Path:
    files = {
        "gt_recipe_maps.json": json.dumps(recipe_maps()).encode() + b"\n",
        "gt_recipes.jsonl": jsonl(recipes()),
        "gt_machines.jsonl": jsonl(machines()),
        "items.jsonl": jsonl(items()),
        "fluids.jsonl": jsonl(fluids()),
    }
    run = home / collect.DATA_EXPORTS / RUN
    run.mkdir(parents=True)
    for rel, data in files.items():
        (run / rel).write_bytes(data)
    manifest = {
        "format": "gregteclink-export/1",
        "run_id": RUN,
        "created_utc": "2026-11-11T12:00:00Z",
        "complete": True,
        "mode": "autoexport",
        "side": "client",
        "pack_version": "2.9.0",
        "gt_version": "5.09.54.265",
        "nei_version": "2.8.172-GTNH",
        "gregteclink": {"version": "abcdef123456"},
        "mods": [
            {"modid": "gregtech", "name": "GregTech", "version": "5.09.54.265",
             "file": "mods/gt.jar", "sha256": "0" * 64},
            {"modid": "testaddon", "name": "Test Addon", "version": "1",
             "file": "mods/addon.jar", "sha256": "1" * 64},
        ],
        "files": {
            rel: {"sha256": hashlib.sha256(d).hexdigest(), "bytes": len(d),
                  "lines": d.count(b"\n")}
            for rel, d in files.items()
        },
        "counts": {"recipe_maps": len(MAPS), "recipes": len(recipes()), "recipe_errors": 0,
                   "machines": len(machines()), "items": len(items()),
                   "fluids": len(fluids()), "recipe_map_errors": 0, "machine_errors": 0,
                   "item_errors": 0, "unregistered": 0},
        "parts": {"gt_dump": "ok", "machines": "ok", "refs": "ok", "nei": "skipped",
                  "autoexport": "ok"},
    }  # fmt: skip
    (run / collect.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    build.build(home)
    return access.db_path(home)


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_home(tmp_path_factory.mktemp("graph-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def ref_of(conn: sqlite3.Connection, map_: str, idx: int) -> str:
    """The citation key of fixture recipe (map, idx): its only (or first) output's maker with
    that map and EU/t and duration."""
    line = next(x for x in recipes() if x["map"] == f"gt.recipe.{map_}" and x["idx"] == idx)
    rows = conn.execute(
        "SELECT r.ref FROM recipe AS r JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " WHERE m.unloc_name = ? AND r.eut = ? AND r.duration_ticks = ?"
        " AND coalesce(r.special_value, 0) = ? ORDER BY r.ref",
        (line["map"], line["eut"], line["duration"], line["special"]),
    ).fetchall()
    assert len(rows) == 1, (map_, idx, rows)
    return str(rows[0][0])


@dataclass(frozen=True)
class Base:
    """A My base for tests (the BaseInputs interface WO-3.8 implements)."""

    tier: int = MV
    owned: Mapping[int, int] = field(default_factory=dict)
    stocked: Mapping[str, float] = field(default_factory=dict)
    lines: Mapping[str, float] = field(default_factory=dict)

    def tier_cap(self) -> int:
        return self.tier

    def owned_machines(self) -> Mapping[int, int]:
        return self.owned

    def stock(self) -> Mapping[str, float]:
        return self.stocked

    def line_outputs(self) -> Mapping[str, float]:
        return self.lines


def build_graph(
    conn: sqlite3.Connection,
    request: Request | None = None,
    base: BaseInputs | None = None,
    templates: chains.Templates = chains.EMPTY,
    **kw: object,
) -> graph.Graph:
    return graph.build(
        conn,
        request or Request(WIDGET),
        base or Base(),
        templates=templates,
        **kw,  # type: ignore[arg-type]
    )


# --- the request ----------------------------------------------------------------------------


def test_request_canonical_form_is_stable_and_round_trips() -> None:
    a = Request(
        WIDGET,
        qty=2,
        unit="/min",
        boundary=(ACID, DUST_B, ACID),
        constraints=Constraints(exclude=("mte:5", "r:gt.recipe.x/0123456789ab", WATER)),
        pins=(Pin(PLATE, "r:gt.recipe.x/0123456789ab"), Pin(DUST_A, "r:gt.recipe.y/a1b2c3d4e5f6")),
    )
    b = Request.from_json(a.canonical())
    assert a == b and a.canonical() == b.canonical() and a.digest() == b.digest()
    data = json.loads(a.canonical())
    assert data["boundary"] == [ACID, DUST_B]  # sorted, once
    assert [p["node"] for p in data["pins"]] == [PLATE, DUST_A][::-1] or [
        p["node"] for p in data["pins"]
    ] == sorted([PLATE, DUST_A])
    assert data["constraints"]["exclude"] == sorted(["mte:5", "r:gt.recipe.x/0123456789ab", WATER])
    assert a.rate_per_s == pytest.approx(2 / 60)
    assert ", " not in a.canonical() and a.canonical() == json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"target": "Widget"}, "resolve it first"),
        ({"target": "gregtech:x@1", "mode": "both"}, "rate or batch"),
        ({"target": WIDGET, "qty": 0}, "above 0"),
        ({"target": WIDGET, "qty": float("nan")}, "finite"),
        ({"target": WIDGET, "unit": "/day"}, "/s, /min or /h"),
        ({"target": WIDGET, "within_s": 60.0}, "batch mode only"),
        ({"target": WIDGET, "mode": "batch"}, "no /unit"),
        ({"target": WIDGET, "tier_cap": 15}, "not a voltage tier"),
        ({"target": WIDGET, "machines": "all"}, r"base or base\+addons"),
        ({"target": WIDGET, "boundary": ("123",)}, "not an item or fluid key"),
        ({"target": WIDGET, "routes": 4}, "1 to 3"),
        (
            {"target": WIDGET, "pins": (Pin(PLATE, "r:a/0123456789ab"),) * 2},
            "one pin per node",
        ),
    ],
)
def test_request_validation(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(RequestError, match=match):
        Request(**kwargs)  # type: ignore[arg-type]


def test_request_helpers_and_batch() -> None:
    assert parse_qty("2/s") == (2.0, "/s") and parse_qty(" 64 ") == (64.0, None)
    assert parse_duration("2h") == 7200 and parse_duration("30min") == 1800
    assert tier_index("IV") == 5 and tier_index("luv") == 6 and tier_index(0) == 0
    with pytest.raises(RequestError):
        tier_index("XV")
    with pytest.raises(RequestError):
        parse_qty("fast")
    batch = Request(WIDGET, mode="batch", qty=64, unit=None, within_s=3600)
    assert batch.rate_per_s is None and Request.from_json(batch.canonical()) == batch
    with pytest.raises(RequestError, match="exclude"):
        Constraints(exclude=("Widget",))
    with pytest.raises(RequestError, match="coil"):
        Constraints(coil_max=14)
    with pytest.raises(RequestError, match="unknown request fields"):
        Request.from_dict({"target": WIDGET, "speed": 2})
    with pytest.raises(RequestError, match="pin recipe"):
        Pin(WIDGET, "mixer/0")


def test_default_base_inputs(tmp_path: Path) -> None:
    db = make_home(tmp_path / "home")
    c = access.connect(db)
    try:
        base = DefaultBaseInputs.from_db(c)
        assert base.tier_cap() == 0  # no quests: the first stage (D64)
        quest_db.set_override(c, "HV")
        base = DefaultBaseInputs.from_db(c)
        assert base.tier_cap() == 3
        assert base.owned_machines() == {} and base.stock() == {}
        assert base.line_outputs() == {}  # D63: nothing stocked by default
        assert isinstance(base, BaseInputs) and isinstance(Base(), BaseInputs)
    finally:
        c.close()


# --- step 1: resolve ------------------------------------------------------------------------


def test_resolve_target(conn: sqlite3.Connection) -> None:
    found = graph.resolve_target(conn, "widget")
    assert isinstance(found, graph.Resolved) and found.key == WIDGET
    assert graph.resolve_target(conn, ACID) == graph.Resolved(ACID, "fluid", "Testacid")
    ask = graph.resolve_target(conn, "dust")  # Dust A or Dust B: ask, never guess
    assert isinstance(ask, prefetch.Clarify)
    assert ask.question.startswith("Which dust did you mean")
    assert {c.key for c in ask.candidates} >= {DUST_A, DUST_B}
    with pytest.raises(graph.GraphError, match="nothing"):
        graph.resolve_target(conn, "zzzqqq")


# --- step 2: build --------------------------------------------------------------------------


def test_candidates_order_and_rejections(conn: sqlite3.Connection) -> None:
    g = build_graph(conn)
    w = g.item(WIDGET)
    cands = {c.recipe: c for c in w.candidates}
    mix0, cr0, cr5 = ref_of(conn, "mixer", 0), ref_of(conn, "chemicalreactor", 0), ref_of(
        conn, "chemicalreactor", 5
    )
    el0, el2, cr4 = ref_of(conn, "electrolyzer", 0), ref_of(conn, "electrolyzer", 2), ref_of(
        conn, "chemicalreactor", 4
    )
    # §7.1: avoid chanced-only; lower tier; fewer inputs (consumed ones; the circuit isn't),
    # then the key.
    order = [cr5, *sorted([cr0, mix0]), el0]
    assert [c.recipe for c in w.candidates if c.status == "chosen"] == order
    assert list(w.chosen) == order  # the target is a primary choice: all of its top 4
    assert cands[el2].status == "rejected" and "above the cap 2" in cands[el2].reason
    assert cands[cr4].status == "rejected" and "D191" in cands[cr4].reason
    assert "fills 2 input slots" in cands[cr4].reason
    assert len(cands) == 7  # the disabled and fake recipes are never candidates
    mix4 = ref_of(conn, "mixer", 4)
    assert cands[mix4].status in ("pruned", "unchecked")  # EU/t 0: ranked last
    assert g.recipe(cr5).flags == ("cleanroom",)  # a single block needs one (D177 (5))
    assert g.recipe(cr0).machines == (102,) and g.recipe(cr0).base
    # The EU pseudo-item: eut x duration per craft, last input.
    assert g.recipe(cr0).inputs[-1] == graph.Port("eu", 30 * 200)
    assert g.item("eu").boundary == "eu"


def test_cleanroom_tier_and_max_eut_constraints(conn: sqlite3.Connection) -> None:
    cr5 = ref_of(conn, "chemicalreactor", 5)
    g = build_graph(conn, Request(WIDGET, constraints=Constraints(cleanroom=False)))
    c = {c.recipe: c for c in g.item(WIDGET).candidates}[cr5]
    assert c.status == "rejected" and "cleanroom" in c.reason
    g = build_graph(conn, Request(WIDGET, tier_cap=1))  # LV: the MV electrolysis drops
    el0 = ref_of(conn, "electrolyzer", 0)
    assert {c.recipe: c for c in g.item(WIDGET).candidates}[el0].status == "rejected"
    g = build_graph(conn, Request(WIDGET, constraints=Constraints(max_eut=20)))
    # Every Widget recipe but the EU/t 0 one draws 30 EU/t or more.
    assert g.item(WIDGET).chosen == (ref_of(conn, "mixer", 4),)
    assert "eu0" in g.recipe(ref_of(conn, "mixer", 4)).flags


def test_exclusions_and_addons(conn: sqlite3.Connection) -> None:
    cr0, mix0 = ref_of(conn, "chemicalreactor", 0), ref_of(conn, "mixer", 0)
    g = build_graph(
        conn, Request(WIDGET, constraints=Constraints(exclude=(cr0, DUST_B, "mte:103")))
    )
    c = {c.recipe: c for c in g.item(WIDGET).candidates}
    assert c[cr0].reason == "excluded"
    assert c[mix0].reason == f"uses excluded {DUST_B}"
    el0 = ref_of(conn, "electrolyzer", 0)
    assert c[el0].status == "rejected" and c[el0].reason.startswith("no machine")
    # The canner is an addon's machine (D12): off for "base", on for "base+addons".
    acid_base = {c.recipe: c for c in build_graph(conn).item(ACID).candidates}
    can = ref_of(conn, "canner", 0)
    assert acid_base[can].status == "rejected" and "addon" in acid_base[can].reason
    acid = {
        c.recipe: c
        for c in build_graph(conn, Request(WIDGET, machines="base+addons")).item(ACID).candidates
    }
    assert acid[can].status != "rejected"


def test_nodes_edges_boundaries_and_loops(conn: sqlite3.Connection) -> None:
    g = build_graph(conn)
    ore = g.item("ore:logTest")
    assert ore.kind == "ore" and ore.members == (LOG_A, LOG_B)
    assert graph.Edge(LOG_A, "ore:logTest", "member", 1.0) in g.edges
    assert g.item(LOG_A).boundary == "raw" and g.item(ORE_A).boundary == "raw"
    assert g.item(WATER).boundary == "raw"  # data/curated/raw_inputs.tsv (D189)
    assert g.item(f"{CIRCUIT}@1").boundary == "catalyst"
    el0 = ref_of(conn, "electrolyzer", 0)
    assert graph.Edge(el0, WIDGET, "out", 0.5) in g.edges  # expected: amount x chance
    assert graph.Edge(DUST_A, ref_of(conn, "mixer", 0), "in", 2.0) in g.edges
    # The acid <-> gas loop is a cycle of the graph (not a tree).
    loop = next(c for c in g.cycles if ACID in c)
    assert GAS in loop and ref_of(conn, "chemicalreactor", 3) in loop
    # The target and its direct inputs that have recipes (Ore A is raw).
    assert g.primary == tuple(sorted({WIDGET, PLATE, DUST_A, DUST_B, ACID}))
    # A recipe chosen for several items is in the graph once, its edges counted once.
    assert len({r.id for r in g.recipes}) == len(g.recipes)


def test_non_primary_nodes_keep_their_first_choice(conn: sqlite3.Connection) -> None:
    # Gas isn't a primary choice: only its first recipe is expanded (D189), the rest of its
    # top four is listed as alternatives a pin can swap in (D61).
    g = build_graph(conn)
    gas = g.item(GAS)
    assert GAS not in g.primary and len(gas.chosen) == 1
    assert all(c.status in ("chosen", "alternative", "pruned", "rejected") for c in gas.candidates)


def test_boundaries_from_my_base_and_the_request(conn: sqlite3.Connection) -> None:
    g = build_graph(conn, base=Base(stocked={PLATE: 64}, lines={DUST_B: 0.5}))
    assert g.item(PLATE).boundary == "stock" and not g.item(PLATE).chosen
    assert g.item(DUST_B).boundary == "line"
    g = build_graph(conn, Request(WIDGET, boundary=(ACID,)))
    assert g.item(ACID).boundary == "request"
    assert all(GAS not in c for c in g.cycles)
    g = build_graph(conn, max_depth=1)
    assert g.item(DUST_A).boundary == "depth" and max(i.depth for i in g.items) <= 1


def test_require_owned(conn: sqlite3.Connection) -> None:
    req = Request(WIDGET, constraints=Constraints(require_owned=True))
    g = build_graph(conn, req, Base(owned={102: 1}))
    chosen = [c.recipe for c in g.item(WIDGET).candidates if c.status == "chosen"]
    assert chosen == [ref_of(conn, "chemicalreactor", 5), ref_of(conn, "chemicalreactor", 0)]


def test_pins(conn: sqlite3.Connection) -> None:
    el0 = ref_of(conn, "electrolyzer", 0)
    g = build_graph(conn, Request(WIDGET, pins=(Pin(WIDGET, el0),)))
    w = g.item(WIDGET)
    assert w.pinned and w.chosen == (el0,)
    assert {c.reason for c in w.candidates if c.status == "pruned"} == {
        "pinned to another recipe"
    }
    el2 = ref_of(conn, "electrolyzer", 2)
    with pytest.raises(RequestError, match="above the cap"):
        build_graph(conn, Request(WIDGET, pins=(Pin(WIDGET, el2),)))
    g = build_graph(conn, Request(WIDGET, pins=(Pin(EMPTY_CELL, el0),)))
    assert any("that node isn't in the graph" in w for w in g.warnings)


def test_same_request_same_graph(db: Path, conn: sqlite3.Connection) -> None:
    first = build_graph(conn)
    other = access.connect(db, readonly=True)
    try:
        again = build_graph(other)
    finally:
        other.close()
    assert first.canonical() == again.canonical() and first.digest() == again.digest()
    assert [i.id for i in first.items] == sorted(i.id for i in first.items)
    assert [e for e in first.edges] == sorted(first.edges, key=lambda e: (e.src, e.dst, e.kind))
    assert first.request == Request(WIDGET).digest()


def test_unknown_target(conn: sqlite3.Connection) -> None:
    with pytest.raises(graph.GraphError, match="not in export run"):
        build_graph(conn, Request("test:item@99"))


def test_supplies_for_gtmath(conn: sqlite3.Connection) -> None:
    rec = build_graph(conn).recipe(ref_of(conn, "mixer", 0))
    # The circuit is held once (Supply(0, 0) would mean it is missing); items per craft.
    assert rec.supplies(3) == (
        Supply(0, 1),
        Supply(2, 6),
        Supply(1, 3),
    )
    acid = build_graph(conn).recipe(ref_of(conn, "chemicalreactor", 0))
    assert Supply(1000, 2000, True) in acid.supplies(2)
    with pytest.raises(ValueError):
        rec.supplies(0)


# --- chain templates ------------------------------------------------------------------------


def write(folder: Path, name: str, rows: list[str]) -> None:
    folder.mkdir(exist_ok=True)
    text = "# test\nitem\trecipe\tbranch\tnote\n" + "".join(f"{r}\n" for r in rows)
    (folder / f"{name}.tsv").write_text(text, encoding="utf-8")


def test_templates_choose_and_mark_branch_points(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    el0, mix0 = ref_of(conn, "electrolyzer", 0), ref_of(conn, "mixer", 0)
    gas = ref_of(conn, "electrolyzer", 1)
    write(tmp_path, "test", [
        f"{WIDGET}\t{el0}\tw\tchanced, but the chain's way",
        f"{WIDGET}\t{mix0}\tw\tor the mixer",
        f"{GAS}\t{gas}\t\tthe loop",
    ])  # fmt: skip
    t = chains.load(tmp_path)
    assert t.chains == ("test",) and t.choices[WIDGET].branch == "w"
    assert chains.validate(t, conn, 1) == []
    g = build_graph(conn, templates=t)
    w = g.item(WIDGET)
    assert w.chosen == (el0, mix0) and w.template == "test" and w.branch == "w"
    assert g.templates == ("test",) and WIDGET in g.primary
    pruned = {c.reason for c in w.candidates if c.status == "pruned"}
    assert pruned == {"chain template test chose others"}


def test_stale_template_rows(conn: sqlite3.Connection, tmp_path: Path) -> None:
    el0, el2 = ref_of(conn, "electrolyzer", 0), ref_of(conn, "electrolyzer", 2)
    write(tmp_path, "stale", [
        f"{WIDGET}\tr:gt.recipe.mixer/000000000000\tw\tgone",
        f"{WIDGET}\t{el2}\tw\tabove the cap here",
        f"{DUST_A}\t{el0}\t\tdoesn't make Dust A",
        f"{ACID}\t{ref_of(conn, 'chemicalreactor', 3)}\t\tfine",
    ])  # fmt: skip
    t = chains.load(tmp_path)
    stale = chains.validate(t, conn, 1)
    assert [(s.item, s.problem) for s in stale] == [
        (WIDGET, "not in the export run"),
        (DUST_A, "doesn't make the item"),
    ]
    assert stale[0].text().startswith("stale.tsv:3: r:gt.recipe.mixer/000000000000")
    g = build_graph(conn, templates=t)
    # el2 is above the cap: the template can't be honoured, so the heuristic decides, warned.
    assert any("none of its recipes" in w for w in g.warnings)
    assert g.item(WIDGET).template is None


@pytest.mark.parametrize(
    ("files", "match"),
    [
        ({"a": ["x\ty"]}, "4 tab-separated"),
        ({"a": [f"Widget\tr:gt.recipe.a/0123456789ab\t\t"]}, "not an item or fluid key"),
        ({"a": [f"{WIDGET}\tmixer/0\t\t"]}, "not an r: key"),
        (
            {"a": [f"{WIDGET}\tr:gt.recipe.a/0123456789ab\t\t"],
             "b": [f"{WIDGET}\tr:gt.recipe.b/0123456789ab\t\t"]},
            "more than one template",
        ),
        (
            {"a": [f"{WIDGET}\tr:gt.recipe.a/0123456789ab\tx\t",
                   f"{WIDGET}\tr:gt.recipe.b/0123456789ab\ty\t"]},
            "different branch ids",
        ),
        (
            {"a": [f"{WIDGET}\tr:gt.recipe.a/0123456789ab\t\t",
                   f"{WIDGET}\tr:gt.recipe.a/0123456789ab\t\t"]},
            "twice",
        ),
    ],
)  # fmt: skip
def test_template_format_errors(
    tmp_path: Path, files: dict[str, list[str]], match: str
) -> None:
    for name, rows in files.items():
        write(tmp_path, name, rows)
    with pytest.raises(ValueError, match=match):
        chains.load(tmp_path)


def test_template_without_header(tmp_path: Path) -> None:
    (tmp_path / "bad.tsv").write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no header"):
        chains.load(tmp_path)
    (tmp_path / "bad.tsv").write_text("item\trecipe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header must be"):
        chains.load(tmp_path)


def test_heuristic_order() -> None:
    def node(ref: str, *ins: str, flags: tuple[str, ...] = (), tier: int = 1,
             chance: int = 10000, base: bool = True, badges: tuple[str, ...] = ()
             ) -> graph.RecipeNode:
        return graph.RecipeNode(
            ref, "gt.recipe.x", "gt", 30, 20, tier,
            tuple(graph.Port(k, 1) for k in ins), (graph.Port("x@1", 1, chance),),
            (1,), base, badges, flags,
        )  # fmt: skip

    nodes = [
        node("r:a/000000000001", "y@1", "z@1"),
        node("r:a/000000000002", "y@1", flags=("recycling",)),
        node("r:a/000000000003", "u@1"),  # undoes a use of x@1
        node("r:a/000000000004", "y@1", chance=5000),
        node("r:a/000000000005", "y@1", tier=2),
        node("r:a/000000000006", "y@1", base=False),
        node("r:a/000000000007", "y@1", badges=("flagged",)),
        node("r:a/000000000008", "y@1", flags=("cells",)),
        node("r:a/000000000009", "y@1"),
    ]
    made_from = frozenset({"u@1"})
    ranked = sorted(nodes, key=lambda n: graph.heuristic("x@1", n, made_from))
    # Recycling, reverse, chanced-only and cells first decide; then tier, inputs, base, badges.
    assert [n.id[-1] for n in ranked] == list("976158432")


def test_shipped_templates_load() -> None:
    t = chains.load()
    assert {"platline", "rare-earths", "naquadah"} <= set(t.chains)
    assert all(r.recipe.startswith("r:") for r in t.rows)
    assert (chains.CHAINS_DIR / "README.md").is_file()


def test_raw_inputs_file(tmp_path: Path) -> None:
    assert WATER in graph.raw_inputs()
    bad = tmp_path / "raw.tsv"
    bad.write_text("key\tnote\nwater\tno prefix\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not an item or fluid key"):
        graph.raw_inputs(bad)


# --- gos graph ------------------------------------------------------------------------------


def run_cli(home: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> tuple[int, str]:
    monkeypatch.setenv("GOS_HOME", str(home))
    parser = argparse.ArgumentParser()
    graphcli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    return graphcli.run(parser.parse_args(["graph", *argv]), out), out.getvalue()


def test_cli(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = db.parent.parent
    code, text = run_cli(home, monkeypatch, "widget", "--tier", "MV")
    assert code == 0 and f"target: Widget [{WIDGET}]  tier cap: 2" in text
    assert "loops, built in" in text
    code, text = run_cli(home, monkeypatch, "dust")
    assert code == 3 and text.startswith("Which dust did you mean")
    code, text = run_cli(home, monkeypatch, WIDGET, "--tier", "2", "--json")
    assert code == 0 and json.loads(text)["target"] == WIDGET
    code, text = run_cli(home, monkeypatch, "--check-chains")
    assert "stale" in text and code == 1  # the shipped templates name pack recipes
    code, text = run_cli(home, monkeypatch)
    assert code == 2
