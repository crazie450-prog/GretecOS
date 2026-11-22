"""One ``gos export collect`` + ``gos db build`` per harness game folder per test session (D340).

Most integration test files need a GOS_HOME holding a DB built from a harness run, exactly as
``collect.collect(game, home, 1, allow_harness=True)`` then ``build.build(home, allow_harness=True)``
leave it. Building it takes 15-65 s each time and about 30 module fixtures did it again for the same
two runs (the server's and the client's). :class:`HomeCache` builds each game folder's home once into
a shared folder and gives every caller its own copy, so a test can still write to its home (plans,
the vault, config) without touching another's.

Under pytest-xdist the workers share the cache: the first to ask for a game folder builds it while
holding a lock (``mkdir`` of ``<key>.lock``, atomic on every OS) and the others wait for it, so each
home is built once per run, not once per worker. A failed build is recorded (``<key>.failed``) so the
waiting workers fail with its message instead of waiting forever; the builder re-raises its own error.

It also holds the order the workers take test files in (:data:`SLOW_FIRST`, :func:`slow_first`).

Not a test file (no ``test_`` prefix): ``conftest.py`` and ``test_harness_cache.py`` load it.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import traceback
from collections.abc import Callable
from pathlib import Path

Builder = Callable[[Path, Path], None]  # (game folder, empty GOS_HOME) -> None

# The integration test files that take longest, slowest first: seconds per file (setup + call +
# teardown, on 4 workers) from a local `-n 4 --dist loadfile` run on 2026-11-18 (D340). Under
# pytest-xdist, conftest.py sorts these to the front so the long files start at once; xdist's own
# order (most tests first, `--loadscope-reorder`) left the 340 s single-test file to the end of
# the run. Unlisted files follow in collection order. A new file that takes more than about a
# minute belongs here (only the order changes: every test still runs).
SLOW_FIRST: dict[str, float] = {
    # WO-11.3: the 10-minute soak plus the designs and the suites it runs beside them (a local
    # serial run on 2026-11-19, D336); first, so it never starts last.
    "test_fake_day_one.py": 1194,
    "test_rank_harness.py": 442,
    "test_power_view_harness.py": 340,
    "test_design_cache_harness.py": 300,  # WO-3.9: every G3 and G4 line three times
    "test_power_objectives_harness.py": 259,
    "test_db_harness.py": 200,
    "test_plan_harness.py": 178,
    "test_g3_lines_harness.py": 144,
    "test_power_effort_harness.py": 127,
    "test_pack_update_harness.py": 114,
    "test_g4_lines_harness.py": 109,
    "test_discord_harness.py": 107,
    "test_crops_harness.py": 103,
    "test_power_harness.py": 95,
    "test_real_dump_harness.py": 95,  # a real structure dump loaded and laid out (local run)
    "test_dashboard_harness.py": 70,
    "test_gtmath_harness.py": 59,
}


def slow_first(names: list[str]) -> list[int]:
    """The order to run items in, given each item's file name: indices into ``names``, the
    files of :data:`SLOW_FIRST` first (slowest first), then the rest; the items of one file keep
    their order (the sort is stable)."""
    return sorted(range(len(names)), key=lambda i: -SLOW_FIRST.get(names[i], 0.0))


class CacheError(RuntimeError):
    pass


def collect_and_build(game: Path, home: Path) -> None:
    """What the fixtures did themselves before D340."""
    from gregtec.db import build
    from gregtec.ingest import collect

    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)


class HomeCache:
    def __init__(
        self,
        root: Path,
        builder: Builder = collect_and_build,
        *,
        timeout_s: float = 1800.0,
        poll_s: float = 0.5,
    ) -> None:
        self.root = root
        self.builder = builder
        self.timeout_s = timeout_s
        self.poll_s = poll_s

    def key(self, game: Path) -> str:
        return hashlib.sha256(str(game.resolve()).encode("utf-8")).hexdigest()[:16]

    def home(self, game: Path, dest: Path) -> Path:
        """``dest`` (missing or empty) becomes a copy of ``game``'s built home; returns ``dest``."""
        built = self._built(game)
        shutil.copytree(built, dest, dirs_exist_ok=True)
        # gos db build's report names the home it was built in: name the copy instead, as a
        # build of its own would have.
        origin = str(self._staging(game)).encode("utf-8")
        for report in (dest / "data" / "reports").glob("*.txt"):
            text = report.read_bytes()
            if origin in text:
                report.write_bytes(text.replace(origin, str(dest).encode("utf-8")))
        return dest

    def _staging(self, game: Path) -> Path:
        """Where ``game``'s home is built (under the lock), before it is renamed into place."""
        return self.root / f"{self.key(game)}.building"

    def _built(self, game: Path) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        key = self.key(game)
        done = self.root / key
        failed = self.root / f"{key}.failed"
        lock = self.root / f"{key}.lock"
        deadline = time.monotonic() + self.timeout_s
        while True:
            if done.is_dir():
                return done
            if failed.is_file():
                raise CacheError(
                    f"building the harness home for {game} failed in another worker:\n"
                    + failed.read_text(encoding="utf-8")
                )
            try:
                lock.mkdir()
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise CacheError(
                        f"timed out after {self.timeout_s:.0f} s waiting for {lock} "
                        f"(the harness home for {game})"
                    ) from None
                time.sleep(self.poll_s)
                continue
            try:
                if done.is_dir():  # built between the check above and the lock
                    return done
                staging = self._staging(game)
                shutil.rmtree(staging, ignore_errors=True)
                try:
                    self.builder(game, staging)
                except BaseException:
                    # Written aside then renamed: a waiter must never read it half written.
                    partial = self.root / f"{key}.failed-partial"
                    partial.write_text(traceback.format_exc(), encoding="utf-8")
                    partial.replace(failed)
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
                staging.rename(done)
                return done
            finally:
                lock.rmdir()
