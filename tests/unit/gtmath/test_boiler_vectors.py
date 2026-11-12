"""Replay GT's own 2.9 large boilers and Tree Growth Simulator (``vectors/boiler.tsv``, written by
``oracle/make_boiler_vectors.py`` from GT 5.09.54.265's classes) through the ports in
``gtmath/boiler.py`` and ``gtmath/treefarm.py`` (WO-4.2, D231, D234)."""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

import pytest

from gregtec.planner.gtmath import boiler, treefarm

VECTORS = Path(__file__).resolve().parent / "vectors" / "boiler.tsv"
LOADS = 1000
TICKS = 4800


def _rows(kind: str) -> list[tuple[str, dict[str, str]]]:
    out = []
    for line in VECTORS.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        k, name, *cols = line.split("\t")
        if k == kind:
            out.append((name, dict(c.split("=", 1) for c in cols)))
    return out


def test_every_29_boiler_is_in_the_vectors() -> None:
    assert {name for name, _ in _rows("boiler")} == set(boiler.BOILERS)
    assert len(_rows("throttle")) == len(boiler.BOILERS) * (boiler.MAX_CIRCUIT + 1)


@pytest.mark.parametrize("name,f", _rows("boiler"), ids=lambda v: str(v)[:40])
def test_boiler_constants_and_runtime_boost(name: str, f: dict[str, str]) -> None:
    spec = boiler.BOILERS[name]
    assert spec.eut == int(f["eut"])
    assert spec.superheated == (f["superheated"] == "true")
    for k, v in f.items():
        if k.startswith("boost_"):
            assert spec.runtime_boost(int(k[6:])) == int(v), k


@pytest.mark.parametrize("name,f", _rows("throttle"), ids=lambda v: str(v)[:40])
def test_throttle_steam_water_and_burn_time(name: str, f: dict[str, str]) -> None:
    spec = boiler.BOILERS[name]
    circuit = int(f["circuit"])
    leut = boiler.throttled_eut(spec, circuit)
    assert leut == int(f["leut"])
    for k, v in f.items():
        if k.startswith("burn_"):
            burn = int(k[5:])
            # adjustBurnTimeForConfig carries its remainder: over many loads, burn x eut / lEUt.
            assert int(v) == LOADS * burn * spec.eut // leut, k
    # The port's steady state at this throttle, on a fuel whose boosted burn time is 591 ticks
    # (any duration does: the throttle scales fuel, steam and water alike).
    duration = next(d for d in range(1, 100_000) if spec.runtime_boost(d) >= 591)
    burn = max(1, spec.runtime_boost(duration))
    fluid = "gasoline" if spec.superheated else "creosote"
    st = boiler.steady(spec, fluid=fluid, duration=duration, circuit=circuit)
    k = boiler.SUPER_TO_NORMAL if spec.superheated else 1
    assert st.steam * k == int(f["steam_per_tick"])
    assert int(f["out_4800"]) == math.floor(st.steam * TICKS)
    assert int(f["water_4800"]) == math.floor(st.water * TICKS)
    # Fuel per tick: 1000 L per stretched burn time, the same EU per litre at every throttle.
    assert st.fuel == Fraction(1000) / (Fraction(burn * spec.eut) / leut)
    if burn == 591:
        measured = Fraction(1000 * LOADS, int(f["burn_591"]))
        assert abs(measured - st.fuel) / st.fuel < Fraction(1, LOADS)


def test_tree_farm_against_gt() -> None:
    ((name, f),) = _rows("treefarm")
    assert name == "MTETreeFarm"
    for t in range(1, 15):
        assert treefarm.tier_multiplier(t) == int(f[f"tier_{t}"])
    # The port reads tier 0 as 1 (getTierExtended's floor is the max(1, ...) in process()).
    assert treefarm.tier_multiplier(1) == int(f["tier_0"])
    assert treefarm.TICKS == int(f["ticks_per_operation"])
    assert treefarm.TOOL_CHARGE == int(f["tool_charge_per_operation"])
    gt = {
        "saw": "logs_saw",
        "buzzsaw": "logs_buzzsaw",
        "chainsaw": "logs_chainsaw",
        "branch_cutter": "saplings_branch_cutter",
        "grafter": "saplings_grafter",
        "shears": "leaves_shear",
        "wire_cutter": "leaves_wire_cutter",
        "electric_wire_cutter": "leaves_electric_wire_cutter",
        "knife": "fruit_knife",
    }
    assert set(gt) == set(treefarm.TOOLS)
    for tool, key in gt.items():
        assert treefarm.TOOLS[tool][1] == int(f[f"tool_multiplier_{key}"]), tool
    # NEI's page lists products with the mode multiplier applied (addFakeRecipeToNEI), the
    # planner's reading; the multipliers are GT's.
    assert [int(f[f"mode_{m}"]) for m in treefarm.MODES] == [5, 5, 2, 1]
