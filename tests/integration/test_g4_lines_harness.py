"""G4's four D65 power benchmark lines, end to end, on a real server harness DB (G4 review, D255).

Plan §8 G4: "4 power benchmark lines (D65), end to end: light-fuel loop, benzene gas turbines,
steam boilers on a tree farm (self-sustaining), naquadah fuel". Each line is designed by the
power designer (WO-4.2) with its effort breakdown (WO-4.3), and the lines that design are
accepted into the EU ledger (WO-4.4), whose per-tier nets go through the distribution checks
(WO-4.5). How each line is asked for (mode, tier, generator kind, fuel keys) is D255.

The light-fuel and benzene lines were strict xfails until WO-4.7 (G4 review finding 1: the
chain's MILP didn't count the EU it drew, so it spent the generator's EU to cut its gated
imports); with the EU stage (D323) they pass D255 (3)'s rule. The EU stage prices only the fuel,
so each candidate also has its direct-import line (the fuel imported as it is, no chain), and
rank.py weighs it against the chains (review H1). The light-fuel line is read as ``best`` at MV,
one gate allowed (D324, pending Clayton, Q-G4-4); what the "self-fed" reading finds on this DB
is pinned by its own test. No option keeps a zero-sum loop (finding 5).

G3 handed G4 the fuel chains of the power lines (G3 row: every distinct (machine, recipe, tier, OC,
parallel) within ±2% of NEI). The chain recipes of the lines that design are checked against the
client harness's NEI dump with G3's check (``test_g3_lines_harness``); OC and parallel are
``gtmath``'s, equal to GT's own classes (D174).

As the other harness tests, these run only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after
both harnesses; locally: mod/README.md) and write nothing outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import designer, ledger, power, solve
from gregtec.planner import distribution as dist
from gregtec.planner.machines import current_run
from gregtec.planner.request import DefaultBaseInputs, Request
from gregtec.vault import deploy as vault_deploy

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"


def _load_g3() -> ModuleType:
    """G3's NEI check, test_g3_lines_harness.py (pytest imports test files by path)."""
    name = "_g4_g3_lines_harness"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("test_g3_lines_harness.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


g3 = _load_g3()


@dataclass(frozen=True)
class Line:
    """One D65 power line as G4 asks for it (D255)."""

    mode: str
    tier: int
    kinds: tuple[str, ...]
    fuels: tuple[str, ...]


# D255: the four lines. Tiers as the phase's own tests and runbooks use them (light fuel at MV as
# in G3's chain, the gas turbine and the tree farm at EV as in WO-4.2/4.3), naquadah at IV, where
# GT's Naquadah Reactor Mk-II (largeNaquadahReactorFuels) is. "Naquadah fuel" is read as GT's
# naquadah reactor fuels (enriched naquadah bolt, rod and long rod) and, since WO-4.8 (D328),
# GoodGenerator's Naquadah Based Liquid Fuel MkI burned in its Large Naquadah Reactor
# (`naquadah_reactor`, D326-D327; MkII and up need a dynamo above IV's).
MK1 = "fluid:naquadah based liquid fuel mki"
LNR = 15537  # MTELargeNaquadahReactor
LINES = {
    "light_fuel": Line("best", 2, ("single_block",), ("fluid:liquid_light_fuel",)),
    "benzene": Line("best", 4, ("gas_turbine",), ("fluid:benzene",)),
    "tree_farm": Line("self_sustaining", 4, ("boiler_steam",), ("fluid:creosote",)),
    "naquadah": Line(
        "best",
        5,
        ("single_block", "naquadah_reactor"),
        (
            "gregtech:gt.metaitem.01@26326",  # enriched naquadah bolt (Mk-I)
            "gregtech:gt.metaitem.01@23326",  # enriched naquadah rod (Mk-II)
            "gregtech:gt.metaitem.02@22326",  # long enriched naquadah rod (Mk-III)
            MK1,  # GoodGenerator's MkI, in the Large Naquadah Reactor (D328)
        ),
    ),
}


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("g4-lines")
    collect.collect(game, h, 1, allow_harness=True)
    result = build.build(h, allow_harness=True)
    assert result.loaded.power_status == "ok", result.loaded.power_error
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


@pytest.fixture(scope="module")
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    yield c
    c.close()


_DESIGNS: dict[str, power.PowerDesign] = {}


def design(conn: sqlite3.Connection, name: str) -> power.PowerDesign:
    """The line's design, once per module (with the effort model, as the CLI runs it)."""
    if name not in _DESIGNS:
        ln = LINES[name]
        req = power.PowerRequest(
            mode=ln.mode, tier_cap=ln.tier, kinds=ln.kinds, fuels=ln.fuels
        )
        _DESIGNS[name] = power.design(conn, req, DefaultBaseInputs(ln.tier))
    return _DESIGNS[name]


def check_option(p: dict[str, Any], ln: Line) -> None:
    """What every option of every line must hold, whatever its numbers."""
    pw = p["power"]
    # The plan keeps each figure to 9 significant digits.
    assert pw["net_eut"] == pytest.approx(
        pw["gross_eut"] - pw["chain_eut"], abs=1e-8 * pw["gross_eut"]
    )
    gen = next(n for n in p["nodes"] if n["kind"] == "generator")
    assert gen["fuel"]["id"] in ln.fuels
    assert gen["fuel"]["ref"].startswith("r:")
    assert all(c.startswith(("mte:", "r:", "rotor:")) for c in pw["generator"]["cites"])
    assert "ok" in p["closed"]  # the validator's verdict is on every option
    eff = p["metrics"]["effort"]
    assert eff["rows"] or eff["uncounted"]  # a breakdown, or what it couldn't count


def check_line(d: power.PowerDesign, ln: Line) -> None:
    """D255 (3)'s pass rule: 2-3 ranked options, each with net EU/t above 0 and a chain under
    99.9% of the generator's output, and each holding ``check_option``."""
    assert 2 <= len(d.options) <= 3, (d.notes, d.rejected)
    assert d.weights.startswith(f"mode {ln.mode}")
    assert [p["rank"]["position"] for p in d.options] == list(
        range(1, len(d.options) + 1)
    )
    for p in d.options:
        check_option(p, ln)
        assert p["power"]["net_eut"] > 0
        assert p["power"]["chain_eut"] < p["power"]["gross_eut"] * 0.999


@pytest.mark.parametrize("name", list(LINES))
def test_the_line_designs_two_or_three_ranked_options(
    conn: sqlite3.Connection, name: str
) -> None:
    check_line(design(conn, name), LINES[name])


def test_the_tree_farm_line_is_proven_closed_with_its_kit(
    conn: sqlite3.Connection,
) -> None:
    for p in design(conn, "tree_farm").options:
        assert p["closed"]["ok"] and p["closed"]["imports"] == []
        assert p["bootstrap"]["seeds_saplings"] and p["bootstrap"]["starter_fuel"]


def test_the_naquadah_line_burns_naquadah_reactors(
    conn: sqlite3.Connection,
) -> None:
    names = {
        r["mte_id"]: r["name_en"]
        for r in conn.execute(
            "SELECT mte_id, name_en FROM machine"
            " WHERE name_en LIKE 'Naquadah Reactor Mk-%' OR mte_id = ?",
            (LNR,),
        )
    }
    assert LNR in names and len(names) > 1
    for p in design(conn, "naquadah").options:
        mte = next(c for c in p["power"]["generator"]["cites"] if c.startswith("mte:"))
        assert int(mte.removeprefix("mte:")) in names


def test_the_naquadah_mk2_line_offers_its_rod_as_imported(
    conn: sqlite3.Connection,
) -> None:
    """Review H1: the EU stage prices only the fuel, so a chain imports a long rod and saws it
    (with a lubricant chain for the saw) rather than import the rod. The plain line, the
    reactor fed rods as imported, is an option of its own, and in the naquadah line it ranks."""
    rod = "gregtech:gt.metaitem.01@23326"
    d = power.design(
        conn,
        power.PowerRequest(
            mode="best", tier_cap=5, kinds=("single_block",), fuels=(rod,)
        ),
        DefaultBaseInputs(5),
        with_effort=False,
    )
    [direct] = [p for p in d.options if p["power"]["direct"]]
    assert [b["id"] for b in direct["boundary"]["in"]] == [rod]
    assert [n["kind"] for n in direct["nodes"]] == ["generator"]
    pw = direct["power"]
    assert pw["chain_eut"] == 0 and pw["net_eut"] == pw["gross_eut"]
    assert any(
        p["power"]["direct"] and p["power"]["fuel"]["id"] == rod
        for p in design(conn, "naquadah").options
    )


def test_the_iv_naquadah_line_runs_the_large_naquadah_reactor_on_mki_gated_by_fusion(
    conn: sqlite3.Connection,
) -> None:
    """WO-4.8's acceptance (D328) as this DB allows it: the IV naquadah line's ranked options
    include the Large Naquadah Reactor on MkI with net EU/t above 0, its liquid air made in the
    line from air (the vacuum freezer, the side chain of D327). MkI itself is not designed: its
    only recipe is a fusion reactor's (LuV, and fusion is not sized, D176), so the chain only
    empties imported MkI cells (a canner), which is importing MkI (review H1). The option says
    so: the fuel is not made, the cell is what it decants, and the gate is MkI with its LuV
    fusion recipe. Acceptance item 2 ("a designed MkI chain") is not met on this DB: blocked
    on fusion sizing (D176, D328)."""
    ranked = [
        p
        for p in design(conn, "naquadah").options
        if p["power"]["generator"]["kind"] == "naquadah_reactor"
    ]
    assert ranked
    chained = [p for p in ranked if not p["power"]["direct"]]
    assert chained, [p["id"] for p in ranked]
    p = chained[0]
    pw = p["power"]
    assert pw["fuel"]["id"] == MK1
    assert pw["net_eut"] > 0 and pw["chain_eut"] < 0.01 * pw["gross_eut"]
    gen = next(n for n in p["nodes"] if n["kind"] == "generator")
    assert gen["machine"]["mte"] == LNR and gen["spec"]["kind"] == "naquadah_reactor"
    dynamo = conn.execute(
        "SELECT tier, eu_out * amps_out FROM dynamo_hatch WHERE mte_id = ? AND run_id = ?",
        (gen["spec"]["dynamo_mte"], current_run(conn)),
    ).fetchone()
    assert dynamo[0] <= 5 and dynamo[1] >= gen["eut"]
    imported = {b["id"] for b in p["boundary"]["in"]}
    assert "fluid:liquidair" not in imported  # made in the line
    assert any(
        "vacuumfreezer" in n["recipe"] for n in p["nodes"] if n["kind"] == "recipe"
    )
    # The fuel: decanted from imported cells, not made; the gate is fusion at LuV.
    src = pw["fuel"]["source"]
    assert not src["made"] and src["by"] == []
    assert src["decanted"] and set(src["decanted"]) <= imported
    gate = src["gate"]
    assert gate["id"] == MK1
    fusion = [
        r for r in gate["recipes"] if r["ref"].startswith("r:gt.recipe.fusionreactor/")
    ]
    assert fusion and all(
        r["tier"] == "LuV" and r["status"] == "rejected" for r in fusion
    )
    assert all("above the cap" in r["reason"] for r in fusion)
    verdict = p["closed"]
    assert verdict["problems"] == [] and not verdict["ok"]
    assert {i["id"] for i in verdict["imports"]} == set(src["decanted"])


def test_the_benzene_chain_draws_well_under_the_turbines_output(
    conn: sqlite3.Connection,
) -> None:
    """WO-4.7's acceptance: the benzene line's chain draws well under the gas turbine's output
    (before the EU stage: 971.999 of 972 EU/t)."""
    for p in design(conn, "benzene").options:
        assert p["power"]["chain_eut"] < 0.25 * p["power"]["gross_eut"]


def zero_sum_loops(p: dict[str, Any]) -> list[tuple[str, str]]:
    """Chain nodes ``b`` that take only from one node ``a`` and give only back to it, while
    ``a`` takes from ``b``: a loop that makes nothing for the line (G4 finding 5: a hammer
    making long rods from the saw's rods, sawn back into rods)."""
    ins: dict[str, set[str]] = {}
    outs: dict[str, set[str]] = {}
    for e in p["edges"]:
        outs.setdefault(e["from"], set()).add(e["to"])
        ins.setdefault(e["to"], set()).add(e["from"])
    chain = {n["n"] for n in p["nodes"] if n["kind"] == "recipe"}
    out = []
    for b in sorted(chain):
        src, dst = ins.get(b, set()), outs.get(b, set())
        if len(src) == 1 and src == dst and b in ins.get(next(iter(src)), set()):
            out.append((next(iter(src)), b))
    return out


@pytest.mark.parametrize("name", list(LINES))
def test_no_option_keeps_a_zero_sum_loop(conn: sqlite3.Connection, name: str) -> None:
    for p in design(conn, name).options:
        assert zero_sum_loops(p) == [], p["id"]
    assert zero_sum_loops(
        {
            "nodes": [{"n": "n1", "kind": "recipe"}, {"n": "n2", "kind": "recipe"}],
            "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n1"}],
        }
    ) == [("n2", "n1"), ("n1", "n2")]


def fewest_gates(conn: sqlite3.Connection, tier: int) -> list[tuple[str, int, float]]:
    """Every light-fuel chain at ``tier`` as the designer solves it (its candidates, fuel
    chains and networks, ``solve.routes``): (generator, the fewest gates of any route, the net
    EU/t of the best route with that many). Stage 1 minimises gates over every primary choice
    at once, so the first route has the fewest; a gate-free route imports nothing the chain
    makes, its fuel included, so its EU-stage value is the EU it draws."""
    req = power.PowerRequest(
        mode="best",
        tier_cap=tier,
        kinds=("single_block",),
        fuels=LINES["light_fuel"].fuels,
    )
    run = current_run(conn)
    base = DefaultBaseInputs(tier)
    gens, _ = power.candidates(conn, run, req, tier)
    out = []
    for gen in power.prune(gens, req.candidates):
        found, _ = power.fuel_chains(conn, gen.fuel, req, base, tier, run, None)
        for chain in found:
            try:
                f = solve.routes(power.network(conn, chain, gen, 1.0, req), 1)[0]
            except solve.SolveError:
                continue
            out.append((gen.id, len(f.gates), gen.eut - (f.eu or 0.0) / 20))
    return out


def test_the_self_fed_reading_of_the_light_fuel_loop(conn: sqlite3.Connection) -> None:
    """Q-G4-4's other reading (D324): a light-fuel line with no gated import. On this DB none
    exists at MV: every MV chain's fewest gates is 1, or its gate-free route nets no EU (review
    L5: shown on the chains themselves, not only on the ranked options). Each MV option is a
    chain with one gated fluid, or the fuel imported as it is (D323). At HV the Turbo Combustion
    Generator's line from oilsands ore and leaves closes with no gate and net EU/t above 0."""
    mv = fewest_gates(conn, 2)
    assert mv
    assert all(gates >= 1 or net <= 0 for _, gates, net in mv), mv
    for p in design(conn, "light_fuel").options:
        if p["power"]["direct"]:
            assert [b["id"] for b in p["boundary"]["in"]] == list(
                LINES["light_fuel"].fuels
            )
        else:
            assert len(p["route"]["gates"]) >= 1
    assert any(gates == 0 and net > 0 for _, gates, net in fewest_gates(conn, 3))
    hv = power.design(
        conn,
        power.PowerRequest(
            mode="best",
            tier_cap=3,
            kinds=("single_block",),
            fuels=LINES["light_fuel"].fuels,
        ),
        DefaultBaseInputs(3),
        with_effort=False,
    )
    fed = [
        p for p in hv.options if not p["route"]["gates"] and not p["power"]["direct"]
    ]
    assert fed and all(p["power"]["net_eut"] > 0 for p in fed)
    assert all(p["power"]["chain_eut"] < p["power"]["gross_eut"] for p in fed)


def chain_recipes(p: dict[str, Any]) -> set[str]:
    """The option's chain recipes. The tree farm's own recipe is left out: NEI shows it with
    its tools as inputs, and it is checked against GT's own class by WO-4.2's oracle vectors
    (``test_boiler_vectors``)."""
    return {n["recipe"] for n in p["nodes"] if n["kind"] == "recipe"}


def nei_hidden(conn: sqlite3.Connection, refs: set[str]) -> set[str]:
    """The refs whose recipe map GT doesn't register with NEI (``recipe_map.nei_visible``
    is 0, e.g. the cutting saw's): NEI has no line to check them against."""
    maps = {r: r.removeprefix("r:").split("/", 1)[0] for r in refs}
    hidden = {
        name
        for (name,) in conn.execute(
            "SELECT unloc_name FROM recipe_map WHERE nei_visible = 0"
        )
    }
    return {r for r, m in maps.items() if m in hidden}


@pytest.fixture(scope="module")
def client(home: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return g3._home(tmp_path_factory.mktemp("g4-client"), "client")


def hand_off(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    """The line's options and, for a line of several fuels, each fuel's own (the ranked 2-3
    of the line can all be one fuel's direct-import lines and NEI-hidden saws: since D323 the
    naquadah line's are)."""
    out = list(design(conn, name).options)
    ln = LINES[name]
    if len(ln.fuels) > 1:
        for fuel in ln.fuels:
            req = power.PowerRequest(
                mode=ln.mode, tier_cap=ln.tier, kinds=ln.kinds, fuels=(fuel,)
            )
            try:
                d = power.design(
                    conn, req, DefaultBaseInputs(ln.tier), with_effort=False
                )
            except power.PowerError:  # no generator burns it at the tier (Mk-III at IV)
                continue
            out.extend(d.options)
    return out


@pytest.mark.parametrize("name", list(LINES))
def test_the_g3_hand_off_chain_recipes_match_nei(
    conn: sqlite3.Connection, client: Path, name: str
) -> None:
    refs = set().union(*(chain_recipes(p) for p in hand_off(conn, name)))
    assert refs and all(r.startswith("r:") for r in refs)
    shown = refs - nei_hidden(conn, refs)
    assert shown, refs  # each line has recipes NEI can check
    if name == "naquadah":
        # WO-4.8 (D328): the Large Naquadah Reactor's chain recipes (MkI decanted from its
        # imported cells, review H1; the liquid air from air) are checked against NEI with
        # the rest.
        lnr = set().union(
            *(
                chain_recipes(p)
                for p in hand_off(conn, name)
                if p["power"]["generator"]["kind"] == "naquadah_reactor"
            )
        )
        assert lnr and lnr <= shown, lnr - shown
    c, run_dir, run_id = g3.golden._open(client)
    try:
        verdicts = g3.check_recipes(c, run_dir, run_id, shown)
    finally:
        c.close()
    bad = [v for v in verdicts.values() if not v.ok]
    assert not bad, g3.golden.report(bad)


def test_accepted_lines_reach_the_ledger_and_the_distribution_checks(
    home: Path, conn: sqlite3.Connection
) -> None:
    tree = design(conn, "tree_farm").options[0]
    naq = design(conn, "naquadah").options[0]
    for p in (tree, naq):
        done = designer.accept_power(home, p)
        assert done.problems == [] and done.ledger["recorded"]
    # A production line at MV, a tier with no source: the ledger warns, distribution chains
    # transformers down from the nearest sourced tier above.
    steel = designer.design(home, Request(STEEL, qty=1, tier_cap="MV")).plans[0]
    assert designer.accept(home, steel["id"]).problems == []

    c = access.connect(access.db_path(home), readonly=True)
    try:
        led = ledger.read(c)
        cat = dist.catalog(c)
    finally:
        c.close()
    assert {ln.entry.plan for ln in led.lines} == {tree["id"], naq["id"], steel["id"]}
    assert led.total("all", "gen_eut") == pytest.approx(
        tree["power"]["net_eut"] + naq["power"]["net_eut"]
    )
    assert any(
        w.kind == "tier_mismatch" and w.tier == "MV" and w.scope == "all"
        for w in led.warnings
    )
    rep = dist.from_ledger(cat, led.tiers["all"], 600)
    [chain] = rep.transformers
    assert chain.steps and chain.steps[0].high == "EV"
    assert chain.steps[-1].low == "MV"
