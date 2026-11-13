"""G4's four D65 power benchmark lines, end to end, on a real server harness DB (G4 review, D255).

Plan §8 G4: "4 power benchmark lines (D65), end to end: light-fuel loop, benzene gas turbines,
steam boilers on a tree farm (self-sustaining), naquadah fuel". Each line is designed by the
power designer (WO-4.2) with its effort breakdown (WO-4.3), and the lines that design are
accepted into the EU ledger (WO-4.4), whose per-tier nets go through the distribution checks
(WO-4.5). How each line is asked for (mode, tier, generator kind, fuel keys) is D255.

The two lines whose chain uses up the generator's whole output on this DB (G4 review finding 1:
the chain's MILP doesn't count the EU it draws, so it spends the generator's EU to cut its gated
imports) are strict xfails: they flip to a failure, to be turned into plain tests, once a fix
lands.

As the other harness tests, these run only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after
the server harness; locally: mod/README.md) and write nothing outside tmp_path.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import designer, ledger, power
from gregtec.planner import distribution as dist
from gregtec.planner.request import DefaultBaseInputs, Request
from gregtec.vault import deploy as vault_deploy

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"


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
# naquadah reactor fuels (enriched naquadah bolt, rod and long rod): GoodGenerator's Large
# Naquadah Reactor, which burns the Naquadah Fuel MkI-VI fluids, is `not_modelled` (D232).
LINES = {
    "light_fuel": Line("best", 2, ("single_block",), ("fluid:liquid_light_fuel",)),
    "benzene": Line("best", 4, ("gas_turbine",), ("fluid:benzene",)),
    "tree_farm": Line("self_sustaining", 4, ("boiler_steam",), ("fluid:creosote",)),
    "naquadah": Line(
        "best",
        5,
        ("single_block",),
        (
            "gregtech:gt.metaitem.01@26326",  # enriched naquadah bolt (Mk-I)
            "gregtech:gt.metaitem.01@23326",  # enriched naquadah rod (Mk-II)
            "gregtech:gt.metaitem.02@22326",  # long enriched naquadah rod (Mk-III)
        ),
    ),
}
FINDING_1 = (
    "G4 finding 1: the chain's MILP doesn't count the EU it draws, so on this DB the chain "
    "uses up the generator's whole output (net 0 or rejected)"
)


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


@pytest.mark.parametrize("name", ["tree_farm", "naquadah"])
def test_the_line_designs_two_or_three_ranked_options(
    conn: sqlite3.Connection, name: str
) -> None:
    d = design(conn, name)
    ln = LINES[name]
    assert 2 <= len(d.options) <= 3, (d.notes, d.rejected)
    assert d.weights.startswith(f"mode {ln.mode}")
    assert [p["rank"]["position"] for p in d.options] == list(
        range(1, len(d.options) + 1)
    )
    for p in d.options:
        check_option(p, ln)
        assert p["power"]["net_eut"] > 0
        assert p["power"]["chain_eut"] < p["power"]["gross_eut"] * 0.999


def test_the_tree_farm_line_is_proven_closed_with_its_kit(
    conn: sqlite3.Connection,
) -> None:
    for p in design(conn, "tree_farm").options:
        assert p["closed"]["ok"] and p["closed"]["imports"] == []
        assert p["bootstrap"]["seeds_saplings"] and p["bootstrap"]["starter_fuel"]


def test_the_naquadah_line_burns_gts_naquadah_reactors(
    conn: sqlite3.Connection,
) -> None:
    names = {
        r["mte_id"]: r["name_en"]
        for r in conn.execute(
            "SELECT mte_id, name_en FROM machine"
            " WHERE name_en LIKE 'Naquadah Reactor Mk-%'"
        )
    }
    assert names
    for p in design(conn, "naquadah").options:
        mte = next(c for c in p["power"]["generator"]["cites"] if c.startswith("mte:"))
        assert int(mte.removeprefix("mte:")) in names


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=FINDING_1)
@pytest.mark.parametrize("name", ["light_fuel", "benzene"])
def test_the_chain_leaves_the_generator_some_output(
    conn: sqlite3.Connection, name: str
) -> None:
    d = design(conn, name)
    assert d.options, d.rejected
    for p in d.options:
        check_option(p, LINES[name])
        assert p["power"]["chain_eut"] < p["power"]["gross_eut"] * 0.999


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
