"""Shared fixtures of the integration tests: the harness home cache (D340, harness_cache.py)."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from collections.abc import Callable
from pathlib import Path

import pytest

_NAME = "_gos_harness_cache"


def _load_cache_module() -> types.ModuleType:
    if _NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _NAME, Path(__file__).with_name("harness_cache.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_NAME]


@pytest.fixture(scope="session")
def built_home(
    tmp_path_factory: pytest.TempPathFactory,
) -> Callable[[Path, Path], Path]:
    """``built_home(game, dest)``: ``dest`` becomes a GOS_HOME as ``collect.collect(game, dest, 1,
    allow_harness=True)`` then ``build.build(dest, allow_harness=True)`` leave it, copied from one
    build per game folder per run, shared by pytest-xdist's workers (D340)."""
    base = tmp_path_factory.getbasetemp()
    # Each xdist worker has its own basetemp (pytest-N/popen-gwK); their parent is the run's.
    root = (
        base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    ) / "harness-homes"
    return _load_cache_module().HomeCache(root).home
