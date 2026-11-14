"""The harness home cache (D340, harness_cache.py): one build per game folder per run, a private
copy for each caller, shared safely by pytest-xdist's workers.

The first tests use a fake builder and run everywhere. The last one runs with
GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job) and checks that a copy from the cache is the same GOS_HOME
a fixture's own collect + build gave before D340: the same files and the same DB rows, and nothing
in it naming the cache's folder.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import sys
import threading
import types
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _module() -> types.ModuleType:
    name = "_gos_harness_cache"  # the name conftest.py loads it under
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("harness_cache.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


hc = _module()


class FakeBuilder:
    def __init__(
        self, delay: threading.Event | None = None, fail: bool = False
    ) -> None:
        self.calls: list[Path] = []
        self.delay = delay
        self.fail = fail
        self.lock = threading.Lock()

    def __call__(self, game: Path, home: Path) -> None:
        with self.lock:
            self.calls.append(game)
        if self.delay is not None:
            assert self.delay.wait(10)
        if self.fail:
            raise ValueError("no harness run")
        (home / "data").mkdir(parents=True)
        (home / "data" / "gos.sqlite").write_text(f"built from {game.name}", "utf-8")
        (home / "data" / "reports").mkdir()
        (home / "data" / "reports" / "db-1.txt").write_text(f"Built {home}\n", "utf-8")


def test_each_game_folder_is_built_once_and_every_caller_gets_its_own_copy(
    tmp_path: Path,
) -> None:
    fake = FakeBuilder()
    cache = hc.HomeCache(tmp_path / "cache", fake)
    a = cache.home(tmp_path / "server", tmp_path / "a")
    b = cache.home(tmp_path / "server", tmp_path / "b")
    c = cache.home(tmp_path / "client", tmp_path / "c")
    assert fake.calls == [tmp_path / "server", tmp_path / "client"]
    assert (a / "data" / "gos.sqlite").read_text("utf-8") == "built from server"
    assert (c / "data" / "gos.sqlite").read_text("utf-8") == "built from client"
    # a test writing to its home changes neither the cache nor another test's copy
    (a / "data" / "gos.sqlite").write_text("changed", "utf-8")
    assert (b / "data" / "gos.sqlite").read_text("utf-8") == "built from server"
    d = cache.home(tmp_path / "server", tmp_path / "d")
    assert (d / "data" / "gos.sqlite").read_text("utf-8") == "built from server"
    assert len(fake.calls) == 2
    # the build report names the copy, as a build of its own would
    assert (d / "data" / "reports" / "db-1.txt").read_text("utf-8") == f"Built {d}\n"


def test_an_existing_empty_destination_is_filled(tmp_path: Path) -> None:
    (tmp_path / "dest").mkdir()  # as tmp_path_factory.mktemp() makes it
    cache = hc.HomeCache(tmp_path / "cache", FakeBuilder())
    assert cache.home(tmp_path / "server", tmp_path / "dest") == tmp_path / "dest"
    assert (tmp_path / "dest" / "data" / "gos.sqlite").is_file()


def _together(n: int, call: Callable[[int], object]) -> list[object]:
    out: list[object] = [None] * n

    def run(i: int) -> None:
        try:
            out[i] = call(i)
        except Exception as exc:  # noqa: BLE001 (the test checks what each caller got)
            out[i] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
        assert not t.is_alive(), "a caller hung"
    return out


def test_callers_at_the_same_time_wait_for_one_build(tmp_path: Path) -> None:
    gate = threading.Event()
    fake = FakeBuilder(delay=gate)
    cache = hc.HomeCache(tmp_path / "cache", fake, poll_s=0.01)
    threading.Timer(0.3, gate.set).start()  # the others are waiting on the lock by then
    homes = _together(4, lambda i: cache.home(tmp_path / "server", tmp_path / f"h{i}"))
    assert fake.calls == [tmp_path / "server"]
    for h in homes:
        assert isinstance(h, Path)
        assert (h / "data" / "gos.sqlite").read_text("utf-8") == "built from server"
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == [
        cache.key(tmp_path / "server")
    ]  # no lock, staging folder or failure marker left


def test_a_failed_build_fails_every_waiting_caller_with_its_error(
    tmp_path: Path,
) -> None:
    gate = threading.Event()
    fake = FakeBuilder(delay=gate, fail=True)
    cache = hc.HomeCache(tmp_path / "cache", fake, poll_s=0.01)
    threading.Timer(0.3, gate.set).start()
    got = _together(3, lambda i: cache.home(tmp_path / "server", tmp_path / f"h{i}"))
    assert len(fake.calls) == 1
    assert sum(isinstance(g, ValueError) for g in got) == 1  # the builder's own error
    waiting = [g for g in got if isinstance(g, hc.CacheError)]
    assert len(waiting) == 2 and all("no harness run" in str(g) for g in waiting)
    assert not any((tmp_path / f"h{i}").exists() for i in range(3))


def test_a_lock_never_released_times_out(tmp_path: Path) -> None:
    cache = hc.HomeCache(tmp_path / "cache", FakeBuilder(), timeout_s=0.2, poll_s=0.01)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / f"{cache.key(tmp_path / 'server')}.lock").mkdir()
    with pytest.raises(hc.CacheError, match="timed out"):
        cache.home(tmp_path / "server", tmp_path / "h")


# --- on a real harness run ------------------------------------------------------------------


def _files(home: Path) -> dict[str, str]:
    """Every file under ``home`` but the DB, by relative path: its sha256, with the home's own
    path (the build report names it) written as ``<home>``."""
    here = str(home).encode("utf-8")
    return {
        p.relative_to(home).as_posix(): hashlib.sha256(
            p.read_bytes().replace(here, b"<home>")
        ).hexdigest()
        for p in sorted(home.rglob("*"))
        if p.is_file() and not p.name.startswith("gos.sqlite")
    }


def _rows(db: Path) -> dict[str, str]:
    """Each table's rows, sorted, hashed (the DB file's own bytes may differ in free pages)."""
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        out = {}
        for t in tables:
            rows = sorted(repr(tuple(r)) for r in conn.execute(f'SELECT * FROM "{t}"'))
            out[t] = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
        return out
    finally:
        conn.close()


def test_a_cached_copy_is_the_home_a_fixture_built_itself(
    tmp_path: Path, built_home: Callable[[Path, Path], Path]
) -> None:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    from gregtec.ingest import collect

    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    own = tmp_path / "own"
    hc.collect_and_build(game, own)
    copy = built_home(game, tmp_path / "copy")
    assert _files(copy) == _files(own)
    db = Path("data", "gos.sqlite")
    assert _rows(copy / db) == _rows(own / db)
    # the cache's folder (where the DB was built) is named nowhere in the copy
    built = hc.HomeCache(Path()).key(game)
    for p in copy.rglob("*"):
        if p.is_file():
            assert built.encode() not in p.read_bytes(), p
