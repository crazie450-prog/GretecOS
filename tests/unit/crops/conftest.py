"""The made-up crop world (tests/fixtures/crops/cropworld.py) for the crop guide's tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "crops" / "cropworld.py"


def load_world() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_wo67_cropworld", FIXTURE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def world() -> ModuleType:
    return load_world()


@pytest.fixture
def db(tmp_path: Path, world: ModuleType) -> Path:
    """GOS_HOME/data/gos.sqlite holding the made-up world."""
    path = tmp_path / "home" / "data" / "gos.sqlite"
    path.parent.mkdir(parents=True)
    return world.write_db(path)
