"""WO-10.5: the line layout planner, its layout JSON, its preview card and ``gos layout``.

Footprints come from WO-1.8's self-authored fixture (tests/fixtures/gtnh-extractor: three made-up
multis, mte 9001-9003, ``fixture:*`` blocks) loaded by ``gos structures load``'s own loader; the
plans are made up here in the canonical plan's shape (nodes, edges, boundary; a power plan's
generator node with its ``machines``). No game data.

The acceptance properties are checked on the layout JSON alone, with this file's own geometry
and flood fill (not the module's): no two keep-outs overlap (nor a keep-out and a corridor or a
chase); every box is inside the bounding volume; every machine's front and a cell of each of its
hatch kinds are reached from the entrance through corridors and service space; the same plans
and options give the same JSON; and the JSON matches ``layout_schema.json``.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import itertools
import json
import sqlite3
from collections import deque
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gregtec import cards, cli
from gregtec.build import footprints as fp
from gregtec.build import layout, layout_cli
from gregtec.db import access
from gregtec.db import structures as structure_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DATASET = FIXTURES / "gtnh-extractor"
SCHEMA = json.loads(
    (Path(layout.__file__).with_name("layout_schema.json")).read_text("utf-8")
)
MULTIS = (9001, 9002, 9003)  # the fixture's multis
UNKNOWN = 9999  # a multi with no structure row

Cell = tuple[int, int, int]


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:", isolation_level=None)
    structure_db.load(c, DATASET)
    try:
        yield c
    finally:
        c.close()


_CONN: list[sqlite3.Connection] = []


def _shared() -> sqlite3.Connection:
    """One fixture DB for the property tests (hypothesis reruns them often)."""
    if not _CONN:
        c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        structure_db.load(c, DATASET)
        _CONN.append(c)
    return _CONN[0]


def node(
    n: str,
    mte: int,
    count: int,
    kind: str = "multiblock",
    tier: str = "HV",
    eut: int = 120,
) -> dict[str, Any]:
    return {
        "n": n,
        "kind": "recipe",
        "recipe": f"r:gt.recipe.test/{int(n[1:]):012x}",
        "machine": {"mte": mte, "kind": kind, "name": f"Machine {mte}", "tier": tier},
        "count": count,
        "eut": eut,
        "eut_avg": float(eut),
    }


def production(pid: str = "pl-steel", **extra: object) -> dict[str, Any]:
    """A made-up steel line: two EBFs fed by a tower and five single blocks."""
    p: dict[str, Any] = {
        "id": pid,
        "name": "Steel line",
        "kind": "production",
        "gt": "5.09.54.265",
        "run": 1,
        "nodes": [
            node("n1", 9001, 2),
            node("n2", 9003, 1),
            node("n3", 50, 5, "singleblock", "LV", 30),
            node("n4", UNKNOWN, 1),
        ],
        "edges": [
            {"from": "n3", "to": "n1", "item": "gregtech:dust@1", "rate_s": 1.5},
            {"from": "n2", "to": "n1", "item": "fluid:oxygen", "rate_s": 2.0},
        ],
        "boundary": {
            "in": [{"id": "minecraft:iron_ingot@0", "rate_s": 3.0, "gated": False}],
            "out": [{"id": "gregtech:ingot@11305", "rate_s": 2.0, "gated": False}],
        },
    }
    p.update(extra)
    return p


def power(pid: str = "pl-power") -> dict[str, Any]:
    """A made-up power plan: two 9002 generators (a gas turbine stand-in) at HV."""
    return {
        "id": pid,
        "name": "Turbines",
        "kind": "power",
        "gt": "5.09.54.265",
        "run": 1,
        "nodes": [
            {
                "n": "n1",
                "kind": "generator",
                "recipe": "gen:test",
                "machine": {
                    "mte": 9002,
                    "kind": "multiblock",
                    "name": "Gen",
                    "tier": "HV",
                },
                "machines": [{"mte": 9002, "name": "Gen", "count": 2, "load": 1.5}],
                "count": 2,
                "eut": 2048,
            },
            node("n2", 60, 1, "singleblock", "HV", 0),
        ],
        "edges": [{"from": "n2", "to": "n1", "item": "fluid:benzene", "rate_s": 0.5}],
        "boundary": {"in": [], "out": []},
        "machines_used": [{"machine": {"mte": 9002, "kind": "multiblock"}, "count": 2}],
        "power": {"generator": {"tier": "HV"}},
    }


# --- the acceptance properties, checked from the JSON alone ---------------------------------


def _box(d: Mapping[str, Any]) -> tuple[Cell, Cell]:
    return tuple(d["lo"]), tuple(d["hi"])  # type: ignore[return-value]


def _overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    (alo, ahi), (blo, bhi) = _box(a), _box(b)
    return all(alo[i] <= bhi[i] and blo[i] <= ahi[i] for i in range(3))


def _inside(c: Cell, d: Mapping[str, Any]) -> bool:
    lo, hi = _box(d)
    return all(lo[i] <= c[i] <= hi[i] for i in range(3))


def _cells(d: Mapping[str, Any]) -> Iterator[Cell]:
    lo, hi = _box(d)
    yield from itertools.product(
        range(lo[0], hi[0] + 1), range(lo[1], hi[1] + 1), range(lo[2], hi[2] + 1)
    )


STEPS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def check(lay: Mapping[str, Any]) -> None:
    """Every acceptance property of one layout."""
    jsonschema.Draft202012Validator(SCHEMA).validate(lay)
    keep = lay["keepouts"]
    free = [*lay["corridors"], *lay["chases"]]
    # no two keep-outs overlap, nor a keep-out and a corridor or chase, nor two of those
    for a, b in itertools.combinations([*keep, *free], 2):
        assert not _overlap(a, b), (a["id"], b["id"])
    # everything is inside the bounding volume, which starts at 0
    bounds = lay["bounds"]
    assert bounds["lo"] == [0, 0, 0]
    assert bounds["size"] == [h + 1 for h in bounds["hi"]]
    for d in [*keep, *free, *lay["entrances"], *lay["placements"]]:
        lo, hi = _box(d)
        assert _inside(lo, bounds) and _inside(hi, bounds), d["id"]
    # each machine sits in its own keep-out with the clearance on every side and above
    by_id = {k["id"]: k for k in keep}
    c, ct = lay["options"]["clearance"], lay["options"]["clearance_top"]
    assert len(keep) == len(lay["placements"])
    for p in lay["placements"]:
        k = by_id[p["keepout"]]
        assert k["of"] == p["id"]
        assert [p["lo"][0] - c, 0, p["lo"][2] - c] == k["lo"]
        assert [p["hi"][0] + c, p["hi"][1] + ct, p["hi"][2] + c] == k["hi"]
        assert p["lo"][1] == 0
        assert _inside(tuple(p["controller"]), p)
    # an entrance is on the bounding volume's face, inside a corridor
    corridors = {x["id"]: x for x in lay["corridors"]}
    for e in lay["entrances"]:
        assert e["side"] == "west" and e["lo"][0] == 0 == e["hi"][0]
        assert all(_inside(cell, corridors[e["corridor"]]) for cell in _cells(e))
    # flood fill from the entrance through corridors and keep-outs minus machine boxes
    open_cells: set[Cell] = set()
    for x in lay["corridors"]:
        open_cells.update(_cells(x))
    machine = {p["id"]: p for p in lay["placements"]}
    for k in keep:
        open_cells.update(
            cell for cell in _cells(k) if not _inside(cell, machine[k["of"]])
        )
    start = [cell for e in lay["entrances"] for cell in _cells(e)]
    seen = set(start)
    todo = deque(start)
    while todo:
        x, y, z = todo.popleft()
        for dx, dy, dz in STEPS:
            n = (x + dx, y + dy, z + dz)
            if n in open_cells and n not in seen:
                seen.add(n)
                todo.append(n)
    for p in lay["placements"]:
        assert tuple(p["front"]) in seen, p["id"]
        kinds = {k for h in p["hatches"] for k in h["kinds"]}
        reached: set[str] = set()
        for h in p["hatches"]:
            x, y, z = h["p"]
            faces = [(x + dx, y + dy, z + dz) for dx, dy, dz in STEPS]
            ok = any(f in seen for f in faces if not _inside(f, p))
            assert h["reached"] == ok
            if ok:
                reached.update(h["kinds"])
        assert reached == kinds, (p["id"], kinds - reached)
        assert p["reach"]["ok"] is True


# --- the layout -----------------------------------------------------------------------------


def test_a_production_line_is_laid_out(conn: sqlite3.Connection) -> None:
    lay = layout.plan_layout(conn, [production()])
    check(lay)
    assert lay["schema"] == "gos-layout" and lay["v"] == 1
    assert lay["plans"] == [
        {"id": "pl-steel", "name": "Steel line", "kind": "production"}
    ]
    # 2 EBFs + 1 tower + 5 single blocks; the unknown multi is not placed, with why
    assert len(lay["placements"]) == 8
    assert lay["unplaced"] == [
        {
            "plan": "pl-steel",
            "node": "n4",
            "mte": UNKNOWN,
            "name": f"Machine {UNKNOWN}",
            "count": 1,
            "why": f"mte:{UNKNOWN} has no footprint: it needs a Lab capture "
            "(gos footprints coverage)",
        }
    ]
    assert any("not placed" in w for w in lay["warnings"])
    # upstream first: the tower and the single blocks before the EBFs they feed
    assert [p["node"] for p in lay["placements"]] == ["n2"] + ["n3"] * 5 + ["n1"] * 2
    # each machine faces its spine; sizes are the footprints' (never estimated)
    lib = fp.library(conn)
    for p in lay["placements"]:
        assert p["facing"] in ("north", "south")
        size = [p["hi"][i] - p["lo"][i] + 1 for i in range(3)]
        if p["kind"] == "multiblock":
            every = lib.for_mte(p["mte"])  # the largest variant by volume (D312)
            big = max(every, key=lambda v: v.size[0] * v.size[1] * v.size[2])
            f = big.rotated(p["facing"])
            assert size == list(f.size)
            assert p["footprint"] == f.key and p["source"] == "gtnh-extractor"
            assert p["variant"] == big.variant
            assert len(p["hatches"]) == len(f.hatch_cells)
        else:
            assert size == [1, 1, 1] and p["source"] == "single block"
            assert p["controller"] == p["lo"]
        assert p["reach"]["ok"]
    ebf = next(p for p in lay["placements"] if p["mte"] == 9001)
    assert ebf["reach"]["maintenance"] is True
    assert "Muffler" in ebf["reach"]["kinds"]


def test_services_carry_the_plans_flows_boundary_and_power(
    conn: sqlite3.Connection,
) -> None:
    lay = layout.plan_layout(conn, [production(), power()])
    check(lay)
    ids = {p["id"]: p for p in lay["placements"]}
    gens = [p["id"] for p in lay["placements"] if p["role"] == "generator"]
    assert len(gens) == 2 and all(ids[g]["mte"] == 9002 for g in gens)
    by = {(s["item"], s["ref"]): s for s in lay["services"]}
    dust = by[("gregtech:dust@1", "plan:pl-steel")]
    assert dust["kind"] == "item" and dust["rate_s"] == 1.5
    assert {ids[i]["node"] for i in dust["from"]} == {"n3"}
    assert {ids[i]["node"] for i in dust["to"]} == {"n1"}
    assert by[("fluid:oxygen", "plan:pl-steel")]["kind"] == "fluid"
    raw = by[("minecraft:iron_ingot@0", "plan:pl-steel")]
    assert raw["from"] == ["e1"] and raw["to"] == []
    assert by[("gregtech:ingot@11305", "plan:pl-steel")]["to"] == ["e1"]
    # EU: HV machines fed by the power plan's HV generators; LV ones by them through a
    # transformer step (no LV generator in the layout), flagged
    hv = by[("eu:HV", "plan:pl-steel")]
    assert hv["kind"] == "eu" and hv["rate_s"] is None and hv["from"] == gens
    assert hv["via_transformer"] is False
    lv = by[("eu:LV", "plan:pl-steel")]
    assert lv["from"] == gens and lv["via_transformer"] is True
    assert any(
        w.startswith("pl-steel eu:LV: no LV generator") and "transformer" in w
        for w in lay["warnings"]
    )
    # a generator's node with eut 0 isn't a consumer
    assert ("eu:HV", "plan:pl-power") not in by


def test_without_a_generator_eu_comes_from_the_base_grid(
    conn: sqlite3.Connection,
) -> None:
    lay = layout.plan_layout(conn, [production()])
    by = {s["item"]: s for s in lay["services"]}
    for tier in ("eu:HV", "eu:LV"):
        assert by[tier]["from"] == ["e1"] and by[tier]["via_transformer"] is False
    assert not any("transformer" in w for w in lay["warnings"])


def test_an_mv_line_on_an_ev_power_plan_needs_a_transformer(
    conn: sqlite3.Connection,
) -> None:
    pw = power()
    pw["power"] = {"generator": {"tier": "EV"}}
    plan = production(
        nodes=[node("n1", 9001, 1, tier="MV"), node("n2", 50, 2, "singleblock", "MV")],
        edges=[{"from": "n2", "to": "n1", "item": "gregtech:dust@1", "rate_s": 1.0}],
        metrics={"eu": [{"tier": "MV", "eut_avg": 200.0, "amps": 2}]},
    )
    lay = layout.plan_layout(conn, [plan, pw])
    check(lay)
    gens = [p["id"] for p in lay["placements"] if p["role"] == "generator"]
    (mv,) = [s for s in lay["services"] if s["kind"] == "eu"]
    assert mv["item"] == "eu:MV" and mv["from"] == gens and mv["to"]
    assert mv["via_transformer"] is True and "e1" not in mv["from"]
    (w,) = [w for w in lay["warnings"] if "eu:MV" in w]
    assert "EV generator(s) " + " ".join(gens) in w
    # QA 10-13: the command runs as printed (gos dist transformer needs --amps)
    assert "(gos dist transformer --from EV --to MV --amps 2)" in w
    # generators at two tiers: one runnable command per source tier, never "<tier>"
    hv = power("pl-power2")
    hv["power"] = {"generator": {"tier": "HV"}}
    lay = layout.plan_layout(conn, [plan, pw, hv])
    (w,) = [w for w in lay["warnings"] if "eu:MV" in w]
    assert "<tier>" not in w
    assert "gos dist transformer --from EV --to MV --amps 2" in w
    assert "gos dist transformer --from HV --to MV --amps 2" in w


def test_a_multi_with_several_variants_is_placed_at_its_largest(
    conn: sqlite3.Connection,
) -> None:
    # the fixture's tower has two variants: t1 3x3x3 and t2 3x4x3 (a taller build)
    plan = production(nodes=[node("n1", 9003, 2)], edges=[], boundary={})
    lay = layout.plan_layout(conn, [plan])
    check(lay)
    for p in lay["placements"]:
        assert p["variant"] == "t2" and "|t2|" in p["footprint"]
        assert p["hi"][1] - p["lo"][1] + 1 == 4
        k = next(k for k in lay["keepouts"] if k["of"] == p["id"])
        assert k["hi"][1] == 4  # the tall build plus clearance_top, never the short one
    (w,) = [w for w in lay["warnings"] if "variant" in w]
    assert "(m1, m2)" in w and "t1 3x3x3" in w and "t2 3x4x3" in w
    assert "largest variant t2 of 2" in w


def test_a_flow_to_an_unplaced_machine_is_warned(conn: sqlite3.Connection) -> None:
    plan = production(
        edges=[{"from": "n3", "to": "n4", "item": "gregtech:dust@2", "rate_s": 1.0}]
    )
    lay = layout.plan_layout(conn, [plan])
    assert not any(s["item"] == "gregtech:dust@2" for s in lay["services"])
    assert (
        "pl-steel n3 -> n4 gregtech:dust@2: not routed, n4 has a machine with no "
        "footprint yet, so it isn't placed" in lay["warnings"]
    )
    assert any("not routed" in n for n in layout.card(lay)["notes"])


def test_a_flow_to_a_step_done_by_hand_says_so(conn: sqlite3.Connection) -> None:
    """QA (harness DB, steel ingot MV): a furnace recipe done by hand has no machine, so its
    flows aren't routed; the warning says that, not that a node was left unplaced."""
    hand = {
        "n": "n5",
        "kind": "recipe",
        "recipe": "r:codechicken.nei.recipe.FurnaceRecipeHandler/0",
        "machine": None,
        "count": None,
        "flags": ["manual"],
    }
    plan = production(
        nodes=[*production()["nodes"], hand],
        edges=[{"from": "n5", "to": "n3", "item": "gregtech:dust@9", "rate_s": 1.0}],
    )
    lay = layout.plan_layout(conn, [plan])
    assert (
        lay["warnings"].count(
            "pl-steel n5 -> n3 gregtech:dust@9: not routed, n5 is a step done by hand "
            "(no machine to place): carry it yourself"
        )
        == 1
    )
    assert not any(u["node"] == "n5" for u in lay["unplaced"])


def test_a_multi_with_no_hatch_data_is_warned(conn: sqlite3.Connection) -> None:
    lib = fp.library(conn)
    f = lib.get(fp.GT_MACHINES, 9001)
    bare = dataclasses.replace(
        f, cells=tuple(dataclasses.replace(c, hatches=()) for c in f.cells)
    )
    assert not bare.hatch_cells
    plan = production(nodes=[node("n1", 9001, 1)], edges=[], boundary={})
    before = layout.plan_layout(conn, [plan])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(layout._Footprints, "_find", lambda self, mte, kind, name: bare)
        lay = layout.plan_layout(conn, [plan])
    (p,) = lay["placements"]
    assert p["reach"]["ok"] is True and p["reach"]["hatch_cells"] == 0
    assert "m1 Machine 9001: no hatch data, hatches not checked" in lay["warnings"]
    # the same plan and options on different footprint data give a different id
    assert lay["id"] != before["id"]


def test_the_layout_id_follows_the_footprints(conn: sqlite3.Connection) -> None:
    plan = production(nodes=[node("n1", 9003, 1)], edges=[], boundary={})
    a = layout.plan_layout(conn, [plan])
    conn.execute("DELETE FROM structure WHERE meta = 9003 AND variant = 't2'")
    b = layout.plan_layout(conn, [plan])  # a new dump: only the short tower is left
    assert b["placements"][0]["variant"] == "t1"
    assert a["id"] != b["id"]


def test_the_same_plans_and_options_give_the_same_layout(
    conn: sqlite3.Connection,
) -> None:
    a = layout.plan_layout(conn, [production()])
    b = layout.plan_layout(conn, [json.loads(json.dumps(production()))])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    c = layout.plan_layout(conn, [production()], layout.Options(corridor_width=2))
    assert c["id"] != a["id"]
    d = layout.plan_layout(conn, [production(name="Renamed")])
    assert d["id"] != a["id"]


def test_long_rows_wrap_into_bands(conn: sqlite3.Connection) -> None:
    plan = production(nodes=[node("n1", 9003, 12)], edges=[], boundary={})
    lay = layout.plan_layout(conn, [plan], layout.Options(max_run=12))
    check(lay)
    spines = [c for c in lay["corridors"] if c["kind"] == "spine"]
    assert (
        len(spines) == 3
    )  # 12 towers, 5 wide each with clearance: 2 per row, 4 per band
    for s in spines:
        assert s["hi"][0] - s["lo"][0] + 1 <= 12
    hall = lay["corridors"][0]
    assert hall["kind"] == "hall" and hall["lo"][2] == spines[0]["lo"][2]
    assert hall["hi"][2] == spines[-1]["hi"][2]
    # every row's keep-outs are flush with its spine
    for p in lay["placements"]:
        spine = next(c for c in lay["corridors"] if c["id"] == p["corridor"])
        k = next(k for k in lay["keepouts"] if k["id"] == p["keepout"])
        if p["facing"] == "south":
            assert k["hi"][2] + 1 == spine["lo"][2]
        else:
            assert k["lo"][2] == spine["hi"][2] + 1
    # the chases run over the corridors, above their headroom
    for h in lay["chases"]:
        under = next(c for c in lay["corridors"] if c["id"] == h["over"])
        assert h["lo"][0::2] == under["lo"][0::2] and h["hi"][0::2] == under["hi"][0::2]
        assert h["lo"][1] == lay["options"]["corridor_height"]


def test_a_machine_wider_than_a_row_gets_its_own_row(conn: sqlite3.Connection) -> None:
    plan = production(nodes=[node("n1", 9003, 3)], edges=[], boundary={})
    lay = layout.plan_layout(conn, [plan], layout.Options(max_run=8, clearance=4))
    check(lay)  # 3 + 8 = 11 wide, past max_run 8: one per row, never dropped
    assert len(lay["placements"]) == 3


def test_a_plan_with_nothing_to_place(conn: sqlite3.Connection) -> None:
    plan = production(nodes=[node("n1", UNKNOWN, 2)], edges=[], boundary={})
    lay = layout.plan_layout(conn, [plan])
    check(lay)
    assert lay["placements"] == [] and len(lay["unplaced"]) == 1
    assert [c["kind"] for c in lay["corridors"]] == ["hall"]


def test_a_front_blocked_by_its_own_structure_is_reported() -> None:
    # A made-up footprint whose controller sits in its middle: the front cell is inside it.
    boxes = {"m1": layout.Box((4, 0, 4), (6, 2, 6))}
    p: dict[str, Any] = {
        "id": "m1",
        "front": [5, 1, 6],
        "hatches": [{"p": [4, 0, 4], "kinds": ["Maintenance"]}],
    }
    keep = [{"id": "k1", "of": "m1", "lo": [3, 0, 3], "hi": [7, 3, 7]}]
    corridors = [{"id": "c1", "lo": [0, 0, 3], "hi": [2, 2, 5]}]
    entrances = [{"id": "e1", "lo": [0, 0, 3], "hi": [0, 2, 5]}]
    layout.reach_all([p], keep, corridors, entrances, boxes)
    assert p["reach"]["front"] is False and p["reach"]["ok"] is False
    assert p["reach"]["maintenance"] is True
    assert "front can't be reached" in layout._reach_problems(p["reach"])[0]


def test_an_enclosed_hatch_cell_is_counted_not_reached() -> None:
    boxes = {"m1": layout.Box((4, 0, 4), (6, 2, 6))}
    p: dict[str, Any] = {
        "id": "m1",
        "front": [5, 0, 3],
        "hatches": [
            {
                "p": [5, 0, 5],
                "kinds": ["Energy"],
            },  # bottom centre: no face out of the box
            {"p": [4, 0, 4], "kinds": ["InputBus"]},
        ],
    }
    keep = [{"id": "k1", "of": "m1", "lo": [3, 0, 3], "hi": [7, 3, 7]}]
    corridors = [{"id": "c1", "lo": [0, 0, 3], "hi": [2, 2, 5]}]
    entrances = [{"id": "e1", "lo": [0, 0, 3], "hi": [0, 2, 5]}]
    layout.reach_all([p], keep, corridors, entrances, boxes)
    r = p["reach"]
    assert r["enclosed"] == 1 and r["reached"] == 1
    assert r["unreached_kinds"] == ["Energy"] and r["ok"] is False
    assert r["maintenance"] is None


def test_options_are_checked() -> None:
    assert layout.Options.parse(["corridor_width=2", "max-run=20"]) == layout.Options(
        corridor_width=2, max_run=20
    )
    for bad in (["corridor_width=0"], ["width=2"], ["max_run=x"], ["clearance"]):
        with pytest.raises(layout.LayoutError):
            layout.Options.parse(bad)
    with pytest.raises(layout.LayoutError, match="clearance_top"):
        layout.Options(clearance_top=0)


def test_plans_are_checked(conn: sqlite3.Connection) -> None:
    with pytest.raises(layout.LayoutError, match="at least one"):
        layout.plan_layout(conn, [])
    with pytest.raises(layout.LayoutError, match="twice"):
        layout.plan_layout(conn, [production(), production()])


def test_a_loop_keeps_every_node(conn: sqlite3.Connection) -> None:
    plan = production(
        nodes=[node("n1", 9001, 1), node("n2", 9003, 1), node("n3", 9002, 1)],
        edges=[
            {"from": "n1", "to": "n2", "item": "a", "rate_s": 1},
            {"from": "n2", "to": "n1", "item": "b", "rate_s": 1},
            {"from": "n3", "to": "n1", "item": "c", "rate_s": 1},
        ],
    )
    lay = layout.plan_layout(conn, [plan])
    check(lay)
    assert [p["node"] for p in lay["placements"]] == ["n3", "n1", "n2"]


# --- property tests -------------------------------------------------------------------------

machines = st.one_of(
    st.tuples(st.sampled_from(MULTIS), st.just("multiblock")),
    st.tuples(st.integers(10, 40), st.sampled_from(["singleblock", "steam"])),
    st.tuples(st.just(UNKNOWN), st.just("multiblock")),
)
options = st.builds(
    layout.Options,
    corridor_width=st.integers(1, 4),
    corridor_height=st.integers(2, 4),
    clearance=st.integers(1, 2),
    clearance_top=st.integers(1, 2),
    chase_height=st.integers(1, 2),
    max_run=st.integers(8, 40),
)


@st.composite
def plans(draw: st.DrawFn) -> list[dict[str, Any]]:
    out = []
    for i in range(draw(st.integers(1, 2))):
        n = draw(st.integers(1, 6))
        nodes = []
        for j in range(n):
            mte, kind = draw(machines)
            nodes.append(node(f"n{j + 1}", mte, draw(st.integers(0, 4)), kind))
        edges = [
            {"from": f"n{a}", "to": f"n{b}", "item": f"i:{a}-{b}", "rate_s": 1.0}
            for a, b in draw(
                st.lists(st.tuples(st.integers(1, n), st.integers(1, n)), max_size=8)
            )
        ]
        out.append(production(f"pl-{i}", nodes=nodes, edges=edges))
    return out


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(plans(), options)
def test_every_layout_holds_the_acceptance_properties(
    ps: list[dict[str, Any]], opts: layout.Options
) -> None:
    conn = _shared()
    lay = layout.plan_layout(conn, ps, opts)
    check(lay)
    assert lay == layout.plan_layout(conn, json.loads(json.dumps(ps)), opts)
    wanted = sum(
        int(n["count"])
        for p in ps
        for n in p["nodes"]
        if n["machine"]["mte"] != UNKNOWN
    )
    assert len(lay["placements"]) == wanted
    for p in lay["placements"]:
        size = p["hi"][0] - p["lo"][0] + 1 + 2 * opts.clearance
        spine = next(c for c in lay["corridors"] if c["id"] == p["corridor"])
        length = spine["hi"][0] - spine["lo"][0] + 1
        assert length <= max(opts.max_run, size)
    card = layout.card(lay)
    assert cards.validate(card) == []


# --- the card -------------------------------------------------------------------------------


def test_the_card_is_a_catalog_card(conn: sqlite3.Connection) -> None:
    lay = layout.plan_layout(conn, [production()])
    c = layout.card(lay)
    assert cards.validate(c) == []
    assert c["type"] == "layout" and c["ref"] == "plan:pl-steel"
    assert c["layout"] == lay["id"]
    sx, sy, sz = lay["bounds"]["size"]
    assert c["size"] == {"x": sx, "y": sy, "z": sz}
    kinds = [r["kind"] for r in c["rects"]]
    assert kinds.count("machine") == 8 and kinds.count("entrance") == 1
    assert kinds.count("keepout") == 8
    m = next(r for r in c["rects"] if r["kind"] == "machine" and r["ref"] == "mte:9001")
    assert m["tier"] == "HV" and m["ok"] is True and m["facing"] in ("north", "south")
    assert m["label"] == "Machine 9001"
    single = next(r for r in c["rects"] if r["ref"] == "mte:50")
    assert single["tier"] == "LV" and (single["w"], single["l"]) == (1, 1)
    assert any("not placed" in n and f"mte:{UNKNOWN}" in n for n in c["notes"])
    assert "8 machines placed, 8 with the front" in c["notes"][0]


def test_the_card_stays_within_its_limits(conn: sqlite3.Connection) -> None:
    plan = production(nodes=[node("n1", 20, 800, "singleblock")], edges=[], boundary={})
    c = layout.card(layout.plan_layout(conn, [plan], layout.Options(max_run=512)))
    assert cards.validate(c) == [] and len(c["rects"]) == layout.MAX_RECTS
    assert "not drawn" in c["notes"][0]


# --- gos layout -----------------------------------------------------------------------------


def _home(tmp_path: Path, *ps: dict[str, Any]) -> Path:
    home = tmp_path / "home"
    db = access.db_path(home)
    db.parent.mkdir(parents=True)
    c = access.connect(db)
    structure_db.load(c, DATASET)
    c.close()
    (home / "data" / "plans").mkdir(parents=True)
    for p in ps:
        (home / "data" / "plans" / f"{p['id']}.json").write_text(
            json.dumps(p), encoding="utf-8"
        )
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f"[paths]\ngos_home = {json.dumps(home.as_posix())}\n", encoding="utf-8"
    )
    return toml


def _gos(toml: Path, *argv: str) -> tuple[int, str]:
    parser = argparse.ArgumentParser()
    layout_cli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = layout_cli.run(
        parser.parse_args(["layout", *argv, "--config", str(toml)]), out
    )
    return code, out.getvalue()


def test_cli(tmp_path: Path) -> None:
    whole = production(nodes=production()["nodes"][:3])
    toml = _home(tmp_path, whole, production("pl-gap"), power())
    code, out = _gos(toml, "pl-steel", "pl-power")
    assert code == 0, out
    assert out.startswith("Layout lo-") and "plan:pl-steel + plan:pl-power" in out
    assert "NOT reached" not in out
    # the only warnings: the tower's variants and the LV machines' transformer step
    warned = [line for line in out.splitlines() if "warning:" in line]
    assert len(warned) == 2, warned
    assert "largest variant t2 of 2" in warned[0]
    assert "gos dist transformer --from HV --to LV" in warned[1]
    code, out = _gos(toml, "pl-steel", "--json")
    check(json.loads(out))
    code, out = _gos(toml, "pl-steel", "--card")
    assert cards.validate(json.loads(out)) == []
    target = tmp_path / "out" / "layout.json"
    target.parent.mkdir()
    code, out = _gos(toml, "pl-steel", "--out", str(target), "--option", "max_run=10")
    assert code == 0 and f"wrote {target}" in out
    written = json.loads(target.read_text("utf-8"))
    assert written["options"]["max_run"] == 10
    check(written)
    # a machine with no footprint: exit 1, and the warning says why
    code, out = _gos(toml, "pl-gap")
    assert code == 1 and "not placed: no footprint" in out
    code, out = _gos(toml, "pl-none")
    assert code == 2 and "isn't saved" in out
    code, out = _gos(toml, "pl-steel", "--option", "max_run=2")
    assert code == 2 and "max_run" in out
    code, out = _gos(toml, "BAD ID")
    assert code == 2 and "not a plan id" in out


def test_cli_without_a_db(tmp_path: Path) -> None:
    toml = _home(tmp_path, production())
    access.db_path(tmp_path / "home").unlink()
    code, out = _gos(toml, "pl-steel")
    assert code == 2 and "gos db build" in out


def test_cli_out_never_writes_into_play_or_the_vault(tmp_path: Path) -> None:
    """QA 10-13: ``--out`` has the gate ``gos shell export --out`` has (D309 (6)), and the
    vault is written only through gregtec.vault."""
    toml = _home(tmp_path, production(), power())
    prism = tmp_path / "instances"
    (prism / "GTNH-Play" / ".minecraft" / "saves" / "World").mkdir(parents=True)
    with toml.open("a", encoding="utf-8") as f:
        f.write(f"prism_instances = {json.dumps(prism.as_posix())}\n")
    play = (
        prism / "GTNH-Play" / ".minecraft" / "saves" / "World" / "sub" / "layout.json"
    )
    me = tmp_path / "home" / "vault" / "me" / "x.json"
    for target in (play, me):
        code, out = _gos(toml, "pl-steel", "--out", str(target))
        assert code == 2 and f"Won't write {target}" in out, out
        assert not target.exists() and not target.parent.exists()
    code, out = _gos(toml, "pl-steel", "--out", str(tmp_path / "elsewhere.json"))
    assert "wrote" in out and (tmp_path / "elsewhere.json").is_file(), out


def test_the_gos_cli_has_the_command() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["layout", "--help"])
    assert exc.value.code == 0


def test_the_api_serves_the_layout_and_its_card(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from gregtec import config
    from gregtec.api.app import Core, create_app
    from gregtec.api.security import Auth
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store

    _home(tmp_path, production(), power())
    home = tmp_path / "home"
    db = Store(home / "data" / "store.sqlite")
    try:
        core = Core(
            cfg=config.Config(gos_home=home),
            store=db,
            auth=Auth(db, "a" * 43),
            meter=UsageMeter(db, 100.0, (80, 100)),
        )
        auth = {"Authorization": "Bearer " + "a" * 43}
        with TestClient(create_app(core), base_url="http://127.0.0.1:8765") as client:
            got = client.get("/api/plans/pl-steel/layout", headers=auth)
            assert got.status_code == 200
            body = got.json()
            check(body["layout"])
            assert body["card"]["layout"] == body["layout"]["id"]
            assert cards.validate(body["card"]) == []
            both = client.get("/api/plans/pl-steel/layout?power=pl-power", headers=auth)
            assert [p["id"] for p in both.json()["layout"]["plans"]] == [
                "pl-steel",
                "pl-power",
            ]
            same = client.get("/api/plans/pl-steel/layout?power=pl-steel", headers=auth)
            assert same.status_code == 400 and "twice" in same.text
            missing = client.get("/api/plans/pl-none/layout", headers=auth)
            assert missing.status_code == 404
            assert (
                client.get("/api/plans/PL..x/layout", headers=auth).status_code == 400
            )
            assert client.get("/api/plans/pl-steel/layout").status_code == 401
    finally:
        db.close()
