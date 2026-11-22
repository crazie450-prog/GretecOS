"""Replay GT's own 2.9 large boilers and Tree Growth Simulator (``vectors/boiler.tsv``, written by
``oracle/make_boiler_vectors.py`` from GT 5.09.54.265's classes) through the ports in
``gtmath/boiler.py`` and ``gtmath/treefarm.py`` (WO-4.2, D231, D234), solid fuels included
(D380: GT's own ``checkProcessing`` on an item of each furnace burn value)."""

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


def test_burntime_ratio_ticks_against_gt() -> None:
    ((name, f),) = _rows("ratio")
    assert name == "LargeBoilerFuelBackend" and len(f) > 1000
    for k, v in f.items():
        assert boiler.burntime_ratio_ticks(int(k[2:])) == int(v), k


def test_the_solids_gt_allows_in_superheated_boilers() -> None:
    ((_, f),) = _rows("allowed")
    assert {k for k, v in f.items() if v == "true"} == {
        k for k in boiler.ALLOWED_FUELS if "@" in k
    }
    assert "false" in f.values()


def test_every_29_boiler_burns_solids_in_the_vectors() -> None:
    for kind, rows in (("solid", 4), ("solid_plain", 1)):
        names = [name for name, _ in _rows(kind)]
        assert sorted(names) == sorted(list(boiler.BOILERS) * rows)


@pytest.mark.parametrize(
    "name,f", _rows("solid") + _rows("solid_plain"), ids=lambda v: str(v)[:60]
)
def test_solid_fuel_burn_time(name: str, f: dict[str, str]) -> None:
    """GT's ``checkProcessing`` loaded ``loads`` items (whole cycles of its carried remainder)
    of each furnace burn value and set a burn counter of twice the burn time for each: their
    sum is twice the port's average x the items, stretched by the throttle (its remainder
    carried, so the sum is the floor). An item GT refused is one the port refuses."""
    spec = boiler.BOILERS[name]
    circuit, loads = int(f["circuit"]), int(f["loads"])
    leut = boiler.throttled_eut(spec, circuit)
    # The item GT doesn't know by a registry name stands for any fuel it doesn't allow.
    item = "minecraft:coal@1" if f["item"].startswith("null@") else f["item"]
    burned = 0
    for k, v in f.items():
        if not k.startswith("v_"):
            continue
        value = int(k[2:])
        if v == "refused":
            with pytest.raises(boiler.BoilerError):
                boiler.steady(spec, item=item, burn_value=value, circuit=circuit)
            continue
        burned += 1
        st = boiler.steady(spec, item=item, burn_value=value, circuit=circuit)
        ticks = st.burn_ticks * loads
        assert ticks.denominator == 1, k
        assert int(v) == 2 * (int(ticks) * spec.eut // leut), k
        # Items per tick: one per stretched burn time, the same EU per item at every throttle.
        assert st.fuel == 1 / (st.burn_ticks * spec.eut / leut)
    allowed = item in boiler.ALLOWED_FUELS or not spec.superheated
    assert (burned > 0) == allowed


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
