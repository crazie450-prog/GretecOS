"""The turbine oracle's vectors, replayed through the port (WO-4.1, D231).

``vectors/rotor.tsv`` and ``vectors/turbine.tsv`` hold what GT 5.09.54.265's own
``TurbineStatCalculator`` and its large turbines' ``fluidIntoPower`` (the 2.9 controllers and the
legacy ones) computed (``oracle/make_turbine_vectors.py`` wrote them by running GT's jar). Every
row must come out of ``gregtec.planner.gtmath.turbine`` the same, to the bit. CLAUDE.md: any
change under ``planner/gtmath`` must keep these green.
"""

from __future__ import annotations

import importlib.util
import math
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.planner.gtmath import GT_COMMIT, GT_VERSION
from gregtec.planner.gtmath.turbine import RotorStats, fluid_into_power

HERE = Path(__file__).resolve().parent
VECTORS = HERE / "vectors"


def _load() -> ModuleType:
    path = HERE / "oracle" / "make_turbine_vectors.py"
    spec = importlib.util.spec_from_file_location("gtmath_make_turbine_vectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MTV = _load()
Fields = dict[str, Any]


def _rows(name: str) -> list[tuple[str, Fields, Fields]]:
    lines = (VECTORS / f"{name}.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith(f"# GT {GT_VERSION} (GT5-Unofficial commit {GT_COMMIT})")
    body = [line for line in lines if not line.startswith("#")]
    assert body[0] == "id\tinputs\toutputs"
    rows = []
    for line in body[1:]:
        case_id, inputs, outputs = line.split("\t")
        rows.append((case_id, MTV.parse_fields(inputs), MTV.parse_fields(outputs)))
    return rows


ROTORS = _rows("rotor")
POWER = _rows("turbine")

# rotor.tsv key -> the port's property.
ROTOR_VALUES = {
    "max_durability": "max_durability",
    "base_eff": "base_efficiency",
    "loose_eff": "loose_efficiency",
    "loose_steam_eff": "loose_steam_efficiency",
    "loose_gas_eff": "loose_gas_efficiency",
    "loose_plasma_eff": "loose_plasma_efficiency",
    "opt_flow": "optimal_flow",
    "opt_steam": "optimal_steam_flow",
    "opt_gas": "optimal_gas_flow",
    "opt_plasma": "optimal_plasma_flow",
    "opt_loose_steam": "optimal_loose_steam_flow",
    "opt_loose_gas": "optimal_loose_gas_flow",
    "opt_loose_plasma": "optimal_loose_plasma_flow",
    "steam_eut": "optimal_steam_eut",
    "gas_eut": "optimal_gas_eut",
    "plasma_eut": "optimal_plasma_eut",
    "loose_steam_eut": "optimal_loose_steam_eut",
    "loose_gas_eut": "optimal_loose_gas_eut",
    "loose_plasma_eut": "optimal_loose_plasma_eut",
    "overflow": "overflow_tier",
}


def _same(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)


@pytest.fixture(scope="module")
def rotors() -> dict[tuple[str, int], RotorStats]:
    return {
        (str(i["material"]), int(i["size"])): MTV.rotor_from(
            o, str(i["material"]), int(i["size"])
        )
        for _, i, o in ROTORS
    }


def test_the_vectors_cover_what_they_should() -> None:
    assert len(ROTORS) >= 800 and len({i["size"] for _, i, _ in ROTORS}) == 4
    assert {(m, s) for m, s in MTV.SETUPS} <= {
        (i["material"], i["size"]) for _, i, _ in ROTORS
    }
    per_turbine = Counter(str(i["turbine"]) for _, i, _ in POWER)
    assert set(per_turbine) == set(MTV.TURBINES)
    assert min(per_turbine.values()) >= 150
    # Three rotor set-ups per controller (WO-4.1's acceptance), tight and loose.
    for turbine in MTV.TURBINES:
        setups = {
            (i["material"], i["size"], i["loose"])
            for case_id, i, _ in POWER
            if case_id.startswith(f"setup:{turbine}:")
        }
        assert len(setups) == 2 * len(MTV.SETUPS), turbine
    # Every path: under, at and over the optimal flow, the dynamo cap, and the weak gas rotor.
    assert sum(1 for _, i, o in POWER if o["eut"] == min(i["cap"], 2**31 - 2)) >= 50
    assert sum(1 for _, i, o in POWER if i["cap"] == 0) >= 20
    assert sum(1 for _, i, o in POWER if o["flow"] == 1 and i["available"] > 1) >= 10
    assert sum(1 for _, i, o in POWER if o["flow"] < i["available"]) >= 300
    assert sum(1 for _, i, o in POWER if 0 < o["flow"] == i["available"]) >= 300


def test_rotor_stats() -> None:
    bad = []
    for case_id, inputs, gt in ROTORS:
        rotor = MTV.rotor_from(gt, str(inputs["material"]), int(inputs["size"]))
        for key, prop in ROTOR_VALUES.items():
            ours = getattr(rotor, prop)
            if isinstance(gt[key], float):
                ok = _same(float(ours), gt[key])
            else:
                ok = ours == gt[key]
            if not ok:
                bad.append((case_id, key, ours, gt[key]))
    assert not bad, bad[:20]


def test_fluid_into_power(rotors: dict[tuple[str, int], RotorStats]) -> None:
    bad = []
    for case_id, i, gt in POWER:
        kind, legacy = MTV.TURBINES[str(i["turbine"])]
        p = fluid_into_power(
            kind,
            rotors[(str(i["material"]), int(i["size"]))],
            available=int(i["available"]),
            cap=int(i["cap"]),
            fuel_value=int(i["fuel"]),
            loose=bool(i["loose"]),
            legacy=legacy,
        )
        got = (p.eut, p.flow, p.optimal_flow)
        want = (gt["eut"], gt["flow"], gt["opt"])
        if got[:2] != want[:2] or not _same(got[2], want[2]):
            bad.append((case_id, got, want))
    assert not bad, f"{len(bad)} of {len(POWER)}: {bad[:10]}"
