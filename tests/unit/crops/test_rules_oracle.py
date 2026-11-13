"""Replay CropsNH's own growth arithmetic (``vectors/cropsnh.tsv``, written by
``oracle/make_vectors.py`` from CropsNH 2.0.140's ``TileEntityCropSticks``) through the port in
``gregtec.crops.rules`` (WO-6.7, D273). Every row must come out the same."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from gregtec.crops import rules as R

VECTORS = Path(__file__).resolve().parent / "vectors" / "cropsnh.tsv"
# CropsNHOracle.java's input grid (the columns of each row)
STORAGE = (0, 1, 9, 10, 11, 19, 20, 49, 50, 51, 90, 91, 99, 100, 101, 150, 200)
MAX_POINTS = 70
MIN_VAR, MAX_VAR = -35, 35


def _rows(kind: str) -> list[list[str]]:
    out = []
    for line in VECTORS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, *cols = line.split("\t")
            if k == kind:
                out.append(cols)
    return out


def _ints(text: str) -> list[int]:
    return [int(v) for v in text.split(",")]


def test_the_vectors_cover_the_grid() -> None:
    assert len(_rows("nutrients")) == 4 * 20 * 2 * len(STORAGE)
    assert len(_rows("rate")) == 18 * 32
    assert len(_rows("variate")) == 14


@pytest.mark.parametrize("liked,humidity,sky,water,values", _rows("nutrients"))
def test_nutrients_per_cycle(
    liked: str, humidity: str, sky: str, water: str, values: str
) -> None:
    want = _ints(values)
    assert len(want) == len(STORAGE)
    got = [
        R.nutrients(int(liked), float(humidity), sky == "true", int(water), f)
        for f in STORAGE
    ]
    assert got == want


@pytest.mark.parametrize("tier,growth,values", _rows("rate"))
def test_growth_rate(tier: str, growth: str, values: str) -> None:
    want = _ints(values)
    assert len(want) == MAX_POINTS + 1
    got = [R.growth_rate(n, int(tier), int(growth)) for n in range(MAX_POINTS + 1)]
    assert got == want


@pytest.mark.parametrize("parents,values", _rows("variate"))
def test_variate_stat(parents: str, values: str) -> None:
    want = _ints(values)
    stats = _ints(parents)
    for v, w in zip(range(MIN_VAR, MAX_VAR + 1), want, strict=True):
        assert R.child_stat(stats, (v,)) == {w: Fraction(1)}, v
