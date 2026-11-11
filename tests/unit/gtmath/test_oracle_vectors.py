"""The GT oracle's vectors, replayed through the port (WO-3.1, D174).

``vectors/*.tsv`` hold what GT 5.09.54.265's own ``OverclockCalculator``, ``ParallelHelper`` and
``ProcessingLogic`` computed for GT's unit-test set-ups, edge cases and a seeded random sample
(``oracle/make_vectors.py`` wrote them by running GT's jar). Every row must come out of
``gregtec.planner.gtmath`` the same, to the bit: EU/t, ticks, overclock counts, parallels, the
batch stretch, the sub-tick multiplier and GT's reasons for refusing a recipe. CLAUDE.md: any
change under ``planner/gtmath`` must keep these green.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import math
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.planner.gtmath import GT_COMMIT, GT_VERSION
from gregtec.planner.gtmath.overclock import (
    calculate,
    heat_discount_multiplier,
    max_allowed_recipe_eut,
    multiplier_under_one_tick,
)
from gregtec.planner.gtmath.parallel import Check, determine
from gregtec.planner.gtmath.processing import process

HERE = Path(__file__).resolve().parent
VECTORS = HERE / "vectors"


def _load_make_vectors() -> ModuleType:
    # The generator owns the vector files' format; tests can't import each other by name.
    path = HERE / "oracle" / "make_vectors.py"
    spec = importlib.util.spec_from_file_location("gtmath_make_vectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MV = _load_make_vectors()
Fields = dict[str, Any]


def _rows(name: str) -> list[tuple[str, Fields, Fields]]:
    rows: list[tuple[str, Fields, Fields]] = []
    lines = (VECTORS / f"{name}.tsv").read_text(encoding="utf-8").splitlines()
    body = [line for line in lines if not line.startswith("#")]
    assert body[0] == "id\tinputs\toutputs"
    for line in body[1:]:
        case_id, inputs, outputs = line.split("\t")
        rows.append((case_id, MV.parse_fields(inputs), MV.parse_fields(outputs)))
    return rows


def _params(name: str) -> list[Any]:
    return [pytest.param(i, o, id=case_id) for case_id, i, o in _rows(name)]


def _same(a: float, b: float) -> bool:
    """Two doubles, bit for bit (NaN equals NaN, 0.0 isn't -0.0)."""
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)


def test_the_vectors_are_of_the_pinned_gt() -> None:
    for name in ("overclock", "parallel", "logic"):
        head = (VECTORS / f"{name}.tsv").read_text(encoding="utf-8").splitlines()[0]
        assert head.startswith(f"# GT {GT_VERSION} (GT5-Unofficial commit {GT_COMMIT})")


def test_the_vectors_cover_what_they_should() -> None:
    oc, par, logic = _rows("overclock"), _rows("parallel"), _rows("logic")
    assert len(oc) >= 700 and len(par) >= 400 and len(logic) >= 300
    assert sum(1 for case_id, *_ in oc if case_id.startswith("gt:")) == 49
    for flag in ("laser_oc", "heat_oc", "heat_discount", "no_overclock", "amperage_oc"):
        assert sum(1 for _, i, _ in oc if i.get(flag)) >= 20, flag
    assert sum(1 for _, i, _ in oc if "duration_under_one_tick" in i) >= 20
    assert (
        sum(1 for _, i, o in par if i.get("batch_size", 1) > 1 and "parallel" in o)
        >= 50
    )
    seen = {o["check"] for _, _, o in par} | {o["check"] for _, _, o in logic}
    assert seen >= {
        "success",
        "none",
        "insufficient_power",
        "insufficient_voltage",
        "insufficient_heat",
        "internal_error",
        "power_overflow",
        "duration_overflow",
    }


@pytest.mark.parametrize(("inputs", "gt"), _params("overclock"))
def test_overclock_calculator(inputs: Fields, gt: Fields) -> None:
    if "error" in gt:
        # GT's setter throws IllegalArgumentException.
        with pytest.raises(ValueError, match="can't be a negative number or zero"):
            MV.overclock_from(inputs)
        return
    oc = MV.overclock_from(inputs)
    before = dataclasses.replace(oc, current_parallel=None)
    assert _same(heat_discount_multiplier(oc), gt["heat_mult"])
    assert max_allowed_recipe_eut(oc.voltage, oc.max_tier_skips) == gt["max_allowed"]
    assert _same(multiplier_under_one_tick(before), gt["mult_before"])
    assert _same(multiplier_under_one_tick(oc), gt["mult_after"])
    result = calculate(oc)
    assert result.eut == gt["eut"]
    assert result.duration == gt["duration"]
    assert result.overclocks == gt["overclocks"]
    assert result.current_parallel == gt["current_parallel"]


@pytest.mark.parametrize(("inputs", "gt"), _params("parallel"))
def test_parallel_helper(inputs: Fields, gt: Fields) -> None:
    result = determine(MV.parallel_from(inputs))
    assert result.check == Check(gt["check"])
    assert result.required == gt.get("required")
    if result.check is Check.NONE:
        return
    if result.check in (Check.INSUFFICIENT_POWER, Check.INSUFFICIENT_VOLTAGE):
        return  # GT stops before it changes its max parallel
    assert result.max_parallel == gt["max_parallel"]
    if result.check is not Check.SUCCESS:
        return
    oc = result.overclock
    assert oc is not None
    assert result.parallel == gt["parallel"]
    assert _same(result.duration_multiplier, gt["duration_multiplier"])
    assert oc.eut == gt["eut"]
    assert oc.duration == gt["duration"]
    assert oc.overclocks == gt["overclocks"]
    assert oc.current_parallel == gt["current_parallel"]
    if "left" in gt:
        # What GT's input consumer took: one craft's worth per parallel, batch ones too.
        took = inputs.get("per_craft", 1) * result.parallel
        assert inputs["available"] - took == gt["left"]


@pytest.mark.parametrize(("inputs", "gt"), _params("logic"))
def test_processing_logic(inputs: Fields, gt: Fields) -> None:
    recipe, logic = MV.logic_from(inputs)
    run = process(recipe, logic, inputs=MV.supplies_from(inputs))
    assert run.check == Check(gt["check"])
    assert run.required == gt.get("required")
    assert run.parallel == gt["parallel"]
    if not run.ok:
        assert (run.eut, run.duration) == (0, 0)
        return
    assert run.eut == gt["eut"]
    assert run.duration == gt["duration"]
    if "left" in gt:
        took = inputs.get("per_craft", 1) * run.parallel
        assert inputs["available"] - took == gt["left"]
