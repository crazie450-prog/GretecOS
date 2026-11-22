"""The core's garbage-collector thresholds (D364, QA 10-13)."""

from __future__ import annotations

import gc
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec import config
from gregtec.api import server


@pytest.fixture
def thresholds() -> Iterator[None]:
    before = gc.get_threshold()
    yield
    gc.set_threshold(*before)


def test_tune_gc_makes_full_collections_rarer(thresholds: None) -> None:
    server.tune_gc()
    assert gc.get_threshold() == server.GC_THRESHOLD == (700, 10, 1000)


class Started(Exception):
    pass


def test_the_core_tunes_the_collector_once_it_holds_its_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thresholds: None
) -> None:
    def tuned() -> None:
        raise Started

    monkeypatch.setattr(server, "tune_gc", tuned)
    monkeypatch.setattr(server, "bind", lambda port: object())
    monkeypatch.setattr(server.supervisor, "elevation_problem", lambda: None)
    monkeypatch.setattr(server.applog, "setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "record_plays", lambda path, cfg: None)
    cfg = config.Config(gos_home=tmp_path / "home")
    assert config.service_problems(cfg) == []
    with pytest.raises(Started):
        server.run(cfg, config_path=tmp_path / "gos.toml")
