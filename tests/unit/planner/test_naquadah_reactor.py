"""The Large Naquadah Reactor in the power designer (WO-4.8, D326-D327), on the WO-3.3 fixture DB
(tests/unit/planner/test_graph.py) plus, for this file only: GoodGenerator's fuel map with one
test fuel (1 L for 30 ticks at 2048 EU/t, giving 1 L of its depleted fluid back), a way to make
the fuel and liquid air from water (two mixer recipes), the reactor's controller, and the rows the
export's power pass would write (its liquid air and boost lists, the dynamo hatches and its
``exotic_hatches`` fact). Every item, recipe, machine and number is made up; no pack data.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.db import access
from gregtec.planner import chains, generators, power, solve, sustain
from gregtec.planner.graph import EU_KEY


def _graph_fixture() -> ModuleType:
    path = Path(__file__).with_name("test_graph.py")
    spec = importlib.util.spec_from_file_location("_wo48_graph_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tg = _graph_fixture()
LNR = 112
LNR_CLASS = "gregtech.common.tileentities.machines.multi.MTELargeNaquadahReactor"
FUEL = "fluid:testnq"
DEPLETED = "fluid:testnq depleted"
AIR = "fluid:liquidair"
FREE = ("fluid:water",)
GG_MAP = "gg.recipe.naquadah_reactor"
BASIC, DURATION, AIR_PER_S = 2048, 30, 100
# Dynamo hatches: (mte, class, tier, volts, amps)
GT_DYN = "gregtech.api.metatileentity.implementations.MTEHatchDynamo"
TT_DYN = "tectech.thing.metaTileEntity.hatch.MTEHatchDynamoMulti"
LASER = "tectech.thing.metaTileEntity.hatch.MTEHatchDynamoTunnel"
BUFFER = (
    "gtPlusPlus.xmod.gregtech.api.metatileentity.implementations.MTEHatchDynamoBuffer"
)
DYNAMOS = (
    (31, GT_DYN, 1, 32, 1),
    (32, GT_DYN, 2, 128, 1),
    (901, BUFFER, 2, 128, 16),
    (15219, TT_DYN, 2, 128, 16),
    (15229, TT_DYN, 2, 128, 64),
    (15232, LASER, 2, 128, 256),
)


def make_home(home: Path) -> Path:
    recipes, machines, maps, fluids, recipe_maps = (
        tg.recipes,
        tg.machines,
        tg.MAPS,
        tg.fluids,
        tg.recipe_maps,
    )

    def more_recipes() -> list[dict[str, object]]:
        return [
            *recipes(),
            tg.gt("mixer", 8, 30, 20, fin=[tg.fl("water", 10)], fout=[tg.fl("testnq", 10)]),
            tg.gt("mixer", 9, 16, 20, fin=[tg.fl("water", 200)], fout=[tg.fl("liquidair", 200)]),
            tg.gt("naquadah_reactor", 0, 0, DURATION, map=GG_MAP, category=GG_MAP,
                  fin=[tg.fl("testnq", 1)], fout=[tg.fl("testnq depleted", 1)],
                  special=BASIC),
        ]  # fmt: skip

    def more_machines() -> list[dict[str, object]]:
        out = machines()
        lnr = dict(out[0])
        lnr.update(
            mte_id=LNR, meta=LNR, tier=None, multiblock=True, name="Large Naquadah Reactor",
            meta_name="multimachine.naquadahreactor", recipe_maps=[GG_MAP],
            **{"class": LNR_CLASS},
        )  # fmt: skip
        hatches = []
        for mte, cls, tier, _, amps in DYNAMOS:
            h = dict(out[0])
            h.update(
                mte_id=mte, meta=mte, tier=tier, name=f"Test Dynamo {tier} {amps}A",
                meta_name=f"hatch.dynamo.{mte}", recipe_maps=[], **{"class": cls},
            )  # fmt: skip
            hatches.append(h)
        return [*out, lnr, *hatches]

    def more_fluids() -> list[dict[str, object]]:
        return [
            *fluids(),
            *(
                {"fluid": f, "unlocalized": f"fluid.{f}", "name": f.title(),
                 "temperature": 300, "density": 1000, "viscosity": 1000, "gaseous": False}
                for f in ("testnq", "testnq depleted", "liquidair")
            ),
        ]  # fmt: skip

    def more_maps() -> list[dict[str, object]]:
        out = recipe_maps()
        gg = dict(out[0])
        gg.update(
            name=GG_MAP,
            backend="gregtech.api.recipe.maps.FuelBackend",
            owner_mod="gregtech",
            categories=[{"name": GG_MAP, "owner_mod": "gregtech"}],
        )
        return [*out, gg]

    tg.recipes, tg.machines, tg.fluids, tg.recipe_maps = (
        more_recipes,
        more_machines,
        more_fluids,
        more_maps,
    )
    tg.MAPS = (*maps, "naquadah_reactor")
    try:
        db = tg.make_home(home)
    finally:
        tg.recipes, tg.machines, tg.MAPS, tg.fluids, tg.recipe_maps = (
            recipes,
            machines,
            maps,
            fluids,
            recipe_maps,
        )
    c = sqlite3.connect(db)
    try:
        run = int(c.execute("SELECT MAX(id) FROM export_run").fetchone()[0])
        rows = [(run, LNR, "air", 0, "liquidair", AIR_PER_S, 0)]
        rows += [
            (run, LNR, "coolant", 0, "testcoolant", 20, 500),
            (run, LNR, "excited", 0, "testexcited", 20, 4),
        ]
        c.executemany("INSERT INTO reactor_boost VALUES (?,?,?,?,?,?,?)", rows)
        c.executemany(
            "INSERT INTO dynamo_hatch VALUES (?,?,?,?,?,?)",
            [(run, m, cls, t, v, a) for m, cls, t, v, a in DYNAMOS],
        )
        mid = c.execute("SELECT id FROM machine WHERE mte_id = ?", (LNR,)).fetchone()[0]
        c.execute(
            "INSERT INTO machine_fact(machine_id, key, value, unit, source, confidence, note)"
            " VALUES (?, 'exotic_hatches', 'any', NULL, 'tooltip', 0.85, 'test')",
            (mid,),
        )
        c.commit()
    finally:
        c.close()
    return db


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_home(tmp_path_factory.mktemp("lnr-home"))


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(db, readonly=True)
    try:
        yield c
    finally:
        c.close()


def _writable_copy(db: Path, tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "copy.db"
    shutil.copyfile(db, path)
    return sqlite3.connect(path)


def _run(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT MAX(id) FROM export_run").fetchone()[0])


# --- the catalog -------------------------------------------------------------------------------


def test_the_catalog_sizes_the_reactor_from_the_db(conn: sqlite3.Connection) -> None:
    run = _run(conn)
    entry = next(e for e in generators.catalog(conn, run) if e.mte_id == LNR)
    assert entry.sizing == "naquadah_reactor" and not entry.legacy
    assert GG_MAP in entry.fuel_maps
    r = generators.reactor(conn, LNR, run)
    assert r is not None and r.air == AIR_PER_S and r.air_fluid == "liquidair"
    assert [b.fluid for b in r.coolants] == ["testcoolant"]
    assert [(b.amount, b.value) for b in r.excited] == [(20, 4)]
    (fuel,) = generators.reactor_fuels(conn, run)
    assert (fuel.fluid, fuel.depleted, fuel.basic, fuel.duration) == (
        "testnq",
        "testnq depleted",
        BASIC,
        DURATION,
    )
    assert fuel.ref.startswith("r:gg.recipe.naquadah_reactor/")
    assert generators.reactor(conn, 999, run) is None


@pytest.mark.parametrize(
    ("eut", "cap", "takes", "want"),
    [
        (2048, 2, "any", 15219),  # MV 16 A holds 2048 exactly; a multi before a laser
        (2049, 2, "any", 15229),  # one more: 64 A
        (8193, 2, "any", 15232),  # past 64 A: the laser
        (2048, 2, "multiamp", 15219),
        (8193, 2, "multiamp", None),  # no laser allowed
        (8193, 2, "laser", 15232),
        (2048, 2, "laser", 15232),  # multi-amp hatches not allowed
        (2048, 2, None, None),  # GT's 1 A hatches only
        (100, 2, None, 32),
        (30, 2, None, 31),  # the lowest tier first
        (2048, 1, "any", None),  # below the cap
    ],
)
def test_the_dynamo_is_the_smallest_the_fact_allows(
    conn: sqlite3.Connection, eut: int, cap: int, takes: str | None, want: int | None
) -> None:
    d = generators.dynamo(conn, _run(conn), eut=eut, tier_cap=cap, takes=takes)
    assert (d.mte_id if d else None) == want
    if d is not None:
        assert d.cap >= eut


def test_the_buffered_dynamo_is_never_offered(conn: sqlite3.Connection) -> None:
    for eut in (100, 2048):
        d = generators.dynamo(conn, _run(conn), eut=eut, tier_cap=2, takes="any")
        assert d is not None and d.mte_id != 901


def test_the_fuel_map_decodes_eu_per_litre(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT unit, value, fuel_value FROM fuel WHERE gt_name = 'naquadahReactorFuels'"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("EU/L", float(BASIC * DURATION), BASIC)]


def test_a_version_5_db_gets_the_reactor_tables(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    access.connect(path).close()
    c = sqlite3.connect(path)
    try:
        c.execute("DROP TABLE reactor_boost")
        c.execute("DROP TABLE dynamo_hatch")
        c.execute("PRAGMA user_version = 5")
        c.commit()
    finally:
        c.close()
    conn = access.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        for table in ("reactor_boost", "dynamo_hatch"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        conn.close()


# --- the designer ------------------------------------------------------------------------------


def _gen(conn: sqlite3.Connection, tier: int = 2) -> power.Gen:
    gens, _ = power.candidates(
        conn,
        _run(conn),
        power.PowerRequest(free=FREE, kinds=("naquadah_reactor",)),
        tier,
    )
    (g,) = gens
    return g


def test_the_candidate_burns_the_fuel_with_its_air_and_gives_back_the_depleted(
    conn: sqlite3.Connection,
) -> None:
    g = _gen(conn)
    assert g.kind == "naquadah_reactor" and g.eut == BASIC and g.tier == 2
    # 1 L per 30-tick run; 2 checks a run (progress 0 and 20), 100 L of liquid air each.
    assert g.fuel == FUEL and g.fuel_per_s == pytest.approx(20 / 30)
    ins, outs = g.ports()
    assert ins == {FUEL: pytest.approx(20 / 30), AIR: pytest.approx(200 * 20 / 30)}
    assert outs == {
        EU_KEY: pytest.approx(BASIC * 20),
        DEPLETED: pytest.approx(20 / 30),
    }
    assert dict(g.spec) == {
        "kind": "naquadah_reactor",
        "mte": LNR,
        "fuel_ref": g.fuel_ref,
        "dynamo_mte": 15219,
    }
    assert g.cites == (f"mte:{LNR}", g.fuel_ref, "mte:15219")
    assert g.eu_per_fuel == pytest.approx(BASIC * DURATION)


def test_no_candidate_without_a_dynamo_that_holds_it(conn: sqlite3.Connection) -> None:
    gens, notes = power.candidates(
        conn, _run(conn), power.PowerRequest(free=FREE, kinds=("naquadah_reactor",)), 1
    )
    assert gens == []
    assert any("needs a dynamo hatch" in n and "LV" in n for n in notes)


def test_no_candidate_from_an_export_without_the_lists(
    db: Path, tmp_path: Path
) -> None:
    c = _writable_copy(db, tmp_path)
    try:
        c.execute("DELETE FROM reactor_boost")
        gens, notes = power.candidates(
            c, 1, power.PowerRequest(free=FREE, kinds=("naquadah_reactor",)), 2
        )
        assert gens == [] and any("no reactor lists" in n for n in notes)
    finally:
        c.close()


def test_the_validator_derives_the_same_ports(conn: sqlite3.Connection) -> None:
    g = _gen(conn)
    node = {"recipe": g.id, "spec": dict(g.spec)}
    p, why = sustain.gen_ports(conn, node)
    assert why is None and p is not None
    ins, outs = g.ports()
    assert p.used == pytest.approx(ins) and p.given == pytest.approx(outs)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"dynamo_mte": 32}, "GT stops the reactor"),
        ({"dynamo_mte": 7}, "not a dynamo hatch"),
        ({"mte": 101}, "not a Large Naquadah Reactor"),
        ({"fuel_ref": "r:gg.recipe.naquadah_reactor/000000000000"}, "not in the DB"),
    ],
)
def test_the_validator_refuses_what_gt_would_not_run(
    conn: sqlite3.Connection, change: dict[str, object], match: str
) -> None:
    g = _gen(conn)
    node = {"recipe": g.id, "spec": {**dict(g.spec), **change}}
    p, why = sustain.gen_ports(conn, node)
    assert p is None and why is not None and match in why


def test_the_line_designs_its_fuel_and_its_air_and_closes_on_water(
    conn: sqlite3.Connection,
) -> None:
    d = power.design(
        conn,
        power.PowerRequest(
            mode="self_sustaining", free=FREE, tier_cap=2, kinds=("naquadah_reactor",)
        ),
        tg.Base(),
        templates=chains.EMPTY,
        with_effort=False,
    )
    assert d.options
    p = d.options[0]
    recipes = {n["recipe"] for n in p["nodes"] if n["kind"] == "recipe"}
    # The fuel's chain and the air's side chain, both from water (D327).
    made = {
        str(r[0])
        for r in conn.execute(
            "SELECT 'fluid:' || f.name FROM recipe AS r JOIN recipe_output AS o"
            " ON o.recipe_id = r.id JOIN fluid AS f ON f.id = o.fluid_id"
            f" WHERE r.ref IN ({','.join('?' * len(recipes))})",
            tuple(recipes),
        )
    }
    assert {FUEL, AIR} <= made
    assert p["closed"]["ok"], p["closed"]
    assert p["power"]["net_eut"] > 0
    gen = next(n for n in p["nodes"] if n["kind"] == "generator")
    assert gen["spec"]["dynamo_mte"] == 15219
    out = {b["id"] for b in p["boundary"]["out"]}
    assert DEPLETED in out  # the depleted fuel: a byproduct the line exports


def test_the_best_line_offers_the_direct_import_too(conn: sqlite3.Connection) -> None:
    d = power.design(
        conn,
        power.PowerRequest(free=FREE, tier_cap=2, kinds=("naquadah_reactor",)),
        tg.Base(),
        templates=chains.EMPTY,
        with_effort=False,
    )
    direct = [p for p in d.options if p["power"]["direct"]]
    chained = [p for p in d.options if not p["power"]["direct"]]
    assert direct and chained
    imports = {b["id"] for b in direct[0]["boundary"]["in"]}
    assert imports == {FUEL, AIR}


def test_a_dynamo_the_line_chose_is_counted_by_its_mte(
    conn: sqlite3.Connection,
) -> None:
    g = _gen(conn)
    p = {
        "nodes": [
            {
                "n": "n1",
                "kind": "generator",
                "count": 1,
                "spec": dict(g.spec),
                "machine": {"mte": LNR},
            }
        ]
    }
    comps, notes, missing = power.parts(conn, p, _run(conn))
    assert any("laser source hatch" in n for n in notes)
    # The hatch the line chose (16 A), not GT's 1 A hatch of its tier.
    assert missing == 0
    assert [(c.key, c.qty, c.part) for c in comps] == [
        ("gregtech:gt.blockmachines@15219", 1, "hatch")
    ]


# --- the solver's boundary at a power line's scale ---------------------------------------------


def test_an_item_both_imported_and_exported_crosses_by_the_difference() -> None:
    imports = (("a", 0.08), ("b", 1.0), ("t", 2.0))
    exports = [("a", 0.0803), ("c", 1.0), ("t", 3.0)]
    i, e = solve._net_out(imports, exports, {"t"})
    assert dict(i) == {"b": 1.0, "t": 2.0}
    assert dict(e) == pytest.approx({"a": 0.0003, "c": 1.0, "t": 3.0})
    i, e = solve._net_out((("a", 0.5),), [("a", 0.2)], set())
    assert dict(i) == pytest.approx({"a": 0.3}) and e == []
