"""Fixtures for the watcher tests (WO-6.1): the fake game tree from ``fakegame.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

FAKEGAME = Path(__file__).with_name("fakegame.py")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gos_watch_fakegame", FAKEGAME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def fg() -> ModuleType:
    """The fakegame module (its NBT writer, file contents and :class:`Tree`)."""
    return _load()


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


@pytest.fixture
def clock() -> Clock:
    return Clock()
