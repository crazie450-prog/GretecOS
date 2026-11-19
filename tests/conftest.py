"""Shared by every test: the planner's process caches start and end empty (WO-3.9, D352), so
no test sees another's component solves or batch readers, on one worker or many."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from gregtec.planner import cache


@pytest.fixture(autouse=True)
def _planner_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()
