"""Fixtures for the wiki tests (WO-8.3): the fake MediaWiki server and a cache over a temp DB."""

from __future__ import annotations

import calendar
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gregtec.db import access
from gregtec.wiki.api import WikiApi
from gregtec.wiki.cache import WikiCache, read_licence

from .fakewiki import FakeWiki, Server


class Sleeps(list[float]):
    """A fake ``time.sleep`` that records each wait."""

    def __call__(self, s: float) -> None:
        self.append(s)


@pytest.fixture
def fake() -> FakeWiki:
    return FakeWiki()


@pytest.fixture
def server(fake: FakeWiki) -> Iterator[Server]:
    srv = Server(fake)
    yield srv
    srv.close()


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "gos.sqlite"
    path.parent.mkdir(parents=True)
    access.connect(path).close()
    return path


NOW = float(
    calendar.timegm(time.strptime("2026-11-11T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
)


@pytest.fixture
def make_api(sleeps: Sleeps) -> Callable[[str], WikiApi]:
    def make(api: str) -> WikiApi:
        # the etiquette's pause is real but short; back-off waits are recorded, not slept
        return WikiApi(api, sleep=sleeps, interval_s=0.0)

    return make


@pytest.fixture
def cache(db: Path, make_api: Callable[[str], WikiApi]) -> WikiCache:
    return WikiCache(db, api_for=make_api, now=lambda: NOW)


@pytest.fixture
def licensed(cache: WikiCache, server: Server) -> WikiCache:
    cache.record_licence(read_licence(cache.api_for(server.api)))
    return cache
